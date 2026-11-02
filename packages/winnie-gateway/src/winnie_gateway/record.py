"""On-disk session records for the model gateway.

One directory per route, one ``-ask`` / ``-answer`` pair per forwarded call, and
one ``-rejected`` file for a call the gateway refused before an upstream was
contacted. All three carry the same session id (the file-name prefix), so a
support question can be answered by handing over one pair instead of
reconstructing it from stdout.

Nothing that authenticates anyone is written down. The caller's Winnie key and
the injected supplier key are stored as a redaction marker, never as a value:
these files exist to explain a call, not to become a second copy of the
credentials. Everything else a diagnosis needs is kept — who called, with which
route and model, which headers were forwarded, the body, the upstream's status,
headers and bytes, the token counts, and how long it took.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request

# Headers that carry a credential (or a session) and therefore never get a value
# in a record. The name stays, so "the caller sent x-api-key" is still visible.
CREDENTIAL_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
        "x-winnie-key",
    }
)

_REDACTED = "<redacted>"

# A refused call's body is kept only up to a size a person will actually read.
_MAX_RECORDED_BODY_BYTES = 8 * 1024

# A public route can be probed without limit. The refusal budget keeps a flood
# from filling the disk while still recording the first few hundred attempts,
# which is where the answer to "what is this client doing" lives.
MAX_REJECTION_RECORDS = 500


def snapshot_body(payload: bytes) -> Any:
    """A request body as a record may keep it: decoded JSON, or truncated text."""
    if not payload:
        return None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return {"binary_bytes": len(payload)}
    if len(text) > _MAX_RECORDED_BODY_BYTES:
        return {"truncated": True, "bytes": len(payload), "text": text[:_MAX_RECORDED_BODY_BYTES]}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def client_request_url(request: Request) -> str:
    """The full URL the client sent, as the client addressed this proxy."""
    return str(request.url)


def key_fingerprint(secret: str) -> str:
    """A stable, non-reversible label for a presented key.

    Used when the call was rejected: the digest tells "the client sent the same
    wrong key twice" without storing the wrong key.
    """
    if not secret:
        return ""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


def redact_headers(
    headers: Iterable[tuple[str, str]] | Mapping[str, str],
) -> dict[str, str]:
    """The headers a record may keep, with credential values replaced."""
    items = headers.items() if isinstance(headers, Mapping) else headers
    redacted: dict[str, str] = {}
    for name, value in items:
        key = name.lower()
        redacted[key] = _REDACTED if key in CREDENTIAL_HEADERS else value
    return redacted


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


class InteractionRecorder:
    """Write one ask/answer pair (or one rejection) per gateway call."""

    def __init__(self, logs_dir: Path, *, service: str = "openai-proxy") -> None:
        self.service = service
        self.dir = logs_dir.resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0
        self._rejections = 0

    # -- forwarded calls --------------------------------------------------

    def begin(
        self,
        *,
        client_url: str,
        upstream_url: str,
        method: str = "POST",
        body: Any = None,
        route: str = "",
        protocol: str = "",
        key_id: str = "",
        key_label: str = "",
        credential_header: str = "",
        model: str = "",
        stream: bool = False,
        client_host: str = "",
        headers: Iterable[tuple[str, str]] | Mapping[str, str] | None = None,
        body_bytes: int = 0,
    ) -> str:
        prefix = self._next_prefix()
        payload: dict[str, Any] = {
            "client": {
                "method": method,
                "url": client_url,
                "client_host": client_host,
            },
            "route": {
                "name": route,
                "protocol": protocol,
                "upstream_url": upstream_url,
            },
            "caller": {
                "key_id": key_id,
                "key_label": key_label,
                "credential_header": credential_header,
            },
            "request": {
                "headers": redact_headers(headers or ()),
                "body_bytes": body_bytes,
                "model": model,
                "stream": stream,
                "body": body,
            },
        }
        self._write(
            prefix,
            "ask",
            payload,
            extra_headers={
                "session": prefix,
                "client_url": client_url,
                "upstream_url": upstream_url,
                "method": method,
            },
        )
        return prefix

    def end_json(
        self,
        prefix: str,
        body: Any,
        *,
        status_code: int | None = None,
        duration_ms: int | None = None,
        upstream_headers: Mapping[str, str] | None = None,
        tokens: Any = None,
    ) -> Path:
        payload: dict[str, Any] = {"body": body}
        self._decorate(
            payload,
            status_code=status_code,
            duration_ms=duration_ms,
            upstream_headers=upstream_headers,
            tokens=tokens,
        )
        return self._write(prefix, "answer", payload, extra_headers={"session": prefix})

    def end_stream(
        self,
        prefix: str,
        *,
        text: str,
        raw_events: list[str],
        status_code: int,
        duration_ms: int | None = None,
        upstream_headers: Mapping[str, str] | None = None,
        tokens: Any = None,
    ) -> Path:
        payload: dict[str, Any] = {
            "stream": True,
            "text": text,
            "event_count": len(raw_events),
            "raw_sse": raw_events,
        }
        self._decorate(
            payload,
            status_code=status_code,
            duration_ms=duration_ms,
            upstream_headers=upstream_headers,
            tokens=tokens,
        )
        return self._write(prefix, "answer", payload, extra_headers={"session": prefix})

    def end_error(
        self,
        prefix: str,
        error: BaseException | str,
        *,
        status_code: int | None = None,
        duration_ms: int | None = None,
        upstream_headers: Mapping[str, str] | None = None,
        upstream_body: Any = None,
    ) -> Path:
        msg = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        payload: dict[str, Any] = {"error": msg}
        if upstream_body is not None:
            payload["upstream_body"] = upstream_body
        self._decorate(
            payload,
            status_code=status_code,
            duration_ms=duration_ms,
            upstream_headers=upstream_headers,
        )
        return self._write(prefix, "answer", payload, extra_headers={"session": prefix})

    # -- refusals ---------------------------------------------------------

    def rejected(
        self,
        *,
        client_url: str,
        method: str,
        status_code: int,
        reason: str,
        route: str = "",
        protocol: str = "",
        headers: Iterable[tuple[str, str]] | Mapping[str, str] | None = None,
        presented_key: str = "",
        key_label: str = "",
        client_host: str = "",
        body: Any = None,
        body_bytes: int = 0,
    ) -> Path | None:
        """Record a call refused before any bytes reached an upstream.

        A 401 or 403 that leaves no trace is the one failure mode nobody can
        debug from the client side: the caller sees a message, and the operator
        has no record of the request that produced it. Returns ``None`` once the
        per-recorder budget is spent, so a probe flood cannot fill the disk.
        """
        with self._lock:
            if self._rejections >= MAX_REJECTION_RECORDS:
                return None
            self._rejections += 1
        prefix = self._next_prefix()
        payload: dict[str, Any] = {
            "client": {
                "method": method,
                "url": client_url,
                "client_host": client_host,
                "headers": redact_headers(headers or ()),
            },
            "route": {"name": route, "protocol": protocol},
            "caller": {
                "key_id": "",
                "key_label": key_label,
                # A digest of the presented key, so "the client is still sending
                # the old one" can be proven without the key being written down.
                "presented_key_digest": key_fingerprint(presented_key),
            },
            "rejection": {
                "status_code": status_code,
                "reason": reason,
                "body_bytes": body_bytes,
                "body": body,
            },
        }
        return self._write(
            prefix,
            "rejected",
            payload,
            extra_headers={
                "session": prefix,
                "client_url": client_url,
                "method": method,
            },
        )

    # -- internals --------------------------------------------------------

    @staticmethod
    def _decorate(
        payload: dict[str, Any],
        *,
        status_code: int | None,
        duration_ms: int | None,
        upstream_headers: Mapping[str, str] | None,
        tokens: Any = None,
    ) -> None:
        if status_code is not None:
            payload["status_code"] = status_code
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if upstream_headers is not None:
            payload["upstream_headers"] = {
                name.lower(): value for name, value in upstream_headers.items()
            }
        counted = _jsonable(tokens)
        if counted is not None:
            payload["tokens"] = counted

    def _next_prefix(self) -> str:
        now = datetime.now()
        with self._lock:
            self._seq += 1
            seq = self._seq
        return f"{now.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{seq:09d}"

    def _write(
        self,
        prefix: str,
        kind: str,
        payload: Any,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> Path:
        path = self.dir / f"{prefix}-{kind}.txt"
        lines = [
            f"# service: {self.service}",
            f"# kind: {kind}",
            f"# session: {prefix}",
            f"# recorded_at: {datetime.now().isoformat(timespec='seconds')}",
        ]
        if extra_headers:
            for key, value in extra_headers.items():
                if key == "session":
                    continue
                lines.append(f"# {key}: {value}")
        header = "\n".join(lines) + "\n\n"
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        # Rewrite the whole file and rename it into place: a half-written record
        # is worse than no record, because it reads like evidence.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(header + body + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return path
