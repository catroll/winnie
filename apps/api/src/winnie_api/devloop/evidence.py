"""The read-only evidence bundle a diagnosis is built from.

Diagnosis is only worth asking for if the model can see what the owner would
see: the failure itself, what the process said around it, the state of the
repository, and whether the *other* services are up. All four are gathered here,
and the bundle is capped, because a diagnosis request that carries a 400 KB log
tail costs more and reasons worse than one that carries the 120 relevant lines.

Nothing in this module mutates anything. Building evidence reads files, runs
read-only git commands and probes health endpoints.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .models import ErrorIncident

# Lines kept from a service's log file. Enough to see the stack around a crash,
# bounded so a chatty run cannot dominate the bundle.
_LOG_LINES = 120
_GIT_TIMEOUT = 5.0


@dataclass(slots=True)
class EvidenceSources:
    """Where the bundle reads from; injected by the host so tests can fake it."""

    repo_root: Path
    log_dir: Path
    service_urls: dict[str, str]


async def build(
    incident: ErrorIncident,
    sources: EvidenceSources,
    *,
    max_bytes: int = 24000,
) -> dict[str, Any]:
    """Assemble and bound one incident's evidence."""
    bundle: dict[str, Any] = {
        "incident": {
            "message": incident.message,
            "kind": incident.kind,
            "source": incident.source,
            "service": incident.service,
            "level": incident.level,
            "exception_type": incident.exception_type,
            "location": incident.location,
            "occurrences": incident.occurrences,
            "first_seen": incident.first_seen.isoformat(),
            "last_seen": incident.last_seen.isoformat(),
            "context": incident.context or {},
            "traceback": incident.traceback or "",
        },
        "repository": await _repository(sources.repo_root),
        "logs": await asyncio.to_thread(
            _logs, sources.log_dir, incident.service
        ),
        "services": await _health(sources.service_urls),
    }
    return _bound(bundle, max_bytes)


async def _repository(repo_root: Path) -> dict[str, Any]:
    """Read-only git facts; a missing or non-git root is not an error."""
    if not (repo_root / ".git").exists():
        return {"available": False, "reason": f"no .git under {repo_root}"}

    async def git(*args: str) -> str:
        def run() -> str:
            try:
                completed = subprocess.run(
                    ["git", *args],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    timeout=_GIT_TIMEOUT,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"(git failed: {exc})"
            output = (completed.stdout or completed.stderr or "").strip()
            return output[:4000]

        return await asyncio.to_thread(run)

    branch, head, status, log, diffstat = await asyncio.gather(
        git("rev-parse", "--abbrev-ref", "HEAD"),
        git("rev-parse", "--short", "HEAD"),
        git("status", "--short"),
        git("log", "--oneline", "-8"),
        git("diff", "--stat"),
    )
    return {
        "available": True,
        "branch": branch,
        "head": head,
        "status": status,
        "recent_commits": log,
        "diff_stat": diffstat,
    }


def _logs(log_dir: Path, service: str) -> dict[str, str]:
    """The tail of the incident service's log, plus the API's when distinct."""
    tails: dict[str, str] = {}
    for name in dict.fromkeys((service, "api")):
        path = log_dir / f"{name}.log"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            tails[name] = f"(unreadable: {exc})"
            continue
        lines = text.splitlines()[-_LOG_LINES:]
        tails[name] = "\n".join(lines)[-12000:]
    return tails


async def _health(service_urls: dict[str, str]) -> dict[str, Any]:
    """Probe each configured service once; never raise."""
    if not service_urls:
        return {}
    results: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=2.0) as client:
        for name, url in service_urls.items():
            try:
                response = await client.get(url)
                results[name] = {"url": url, "status": response.status_code}
            except httpx.HTTPError as exc:
                results[name] = {"url": url, "error": f"{type(exc).__name__}: {exc}"}
    return results


def _bound(bundle: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    """Shrink the bundle until it fits, dropping evidence in value order.

    Log tails go first (they are the largest and the most redundant), then a
    truncated traceback. The incident itself is never dropped.
    """
    def size() -> int:
        return len(json.dumps(bundle, ensure_ascii=False, default=str).encode())

    if size() <= max_bytes:
        return bundle

    logs = bundle.get("logs") or {}
    for name in sorted(logs, key=lambda key: len(logs[key]), reverse=True):
        while size() > max_bytes and len(logs[name]) > 2000:
            logs[name] = logs[name][: len(logs[name]) // 2]
    if size() > max_bytes:
        traceback = bundle["incident"].get("traceback", "")
        if traceback:
            bundle["incident"]["traceback"] = traceback[:4000]
    if size() > max_bytes:
        bundle["repository"] = {
            key: value
            for key, value in (bundle.get("repository") or {}).items()
            if key in ("available", "branch", "head")
        }
    return bundle
