#!/usr/bin/env python3
"""Keep the local services alive, and leave a record when one dies.

The API cannot observe its own death, so supervision is a separate process. This
one polls each service's health endpoint, restarts a dead one through
``scripts/services.sh restart <name>`` with an exponential backoff, and writes
what it saw into a spool directory that the API drains into incidents. Writing to
the spool rather than to the API is deliberate: the incident that matters most is
"the API was down", and that cannot be posted to the API at the moment it
happens.

Only stdlib, no imports from the application: a supervisor that shares code with
its supervisee fails when the checkout is broken.

    make watchdog                     # supervise api,index,admin every 15s
    python3 scripts/watchdog.py --once --verbose
    python3 scripts/watchdog.py --services api --interval 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Probe target and log file per service. The health path is the contract each
# service already implements; the admin console answers 200 on `/`.
DEFAULT_PROBES = {
    "api": "http://127.0.0.1:8000/healthz",
    "index": "http://127.0.0.1:8790/healthz",
    "admin": "http://127.0.0.1:5173/",
}

DEFAULT_BACKOFF = (5, 15, 45, 120, 300)


def log(message: str, *, verbose: bool = True) -> None:
    if verbose:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        print(f"{stamp} watchdog: {message}", flush=True)


def probe(url: str, timeout: float) -> tuple[bool, str]:
    """Return (healthy, detail). Any transport failure is unhealthy."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 400, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def tail_log(path: Path, lines: int = 60) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])[-8000:]


class Spool:
    """Append-only JSON lines the API drains and deletes.

    One file per writing process, flushed and fsynced per event, so a watchdog
    killed mid-run still leaves whole lines behind.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / f"{int(time.time())}-{os.getpid()}.jsonl"
        directory.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)

    def write(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


def event(
    kind: str,
    service: str,
    message: str,
    *,
    level: str = "error",
    context: dict | None = None,
) -> dict:
    return {
        "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "source": "watchdog",
        "kind": kind,
        "service": service,
        "level": level,
        "message": message,
        "context": context or {},
    }


def restart(service: str, root: Path) -> tuple[bool, str]:
    """Restart one service through the script that owns the process contract."""
    import subprocess

    try:
        completed = subprocess.run(
            # `bash` explicitly: the checkout's exec bit is not part of the
            # contract, and a supervisor that dies on EACCES supervises nothing.
            ["bash", str(root / "scripts" / "services.sh"), "restart", service],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "restart timed out after 180s"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode == 0, output[-2000:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supervise the local Winnie services")
    parser.add_argument(
        "--services",
        default="api,index,admin",
        help="comma-separated services to watch (default: api,index,admin)",
    )
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument(
        "--max-attempts", type=int, default=5, help="restart attempts before a cooldown"
    )
    parser.add_argument(
        "--cooldown", type=float, default=600.0, help="seconds to stop retrying a service"
    )
    parser.add_argument("--timeout", type=float, default=3.0, help="probe timeout")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--spool-dir", default="")
    parser.add_argument("--log-dir", default="")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    spool = Spool(Path(args.spool_dir) if args.spool_dir else root / "data/devloop/spool")
    log_dir = Path(args.log_dir) if args.log_dir else root / ".cache/logs"
    names = [name.strip() for name in args.services.split(",") if name.strip()]
    unknown = [name for name in names if name not in DEFAULT_PROBES]
    if unknown:
        print(f"watchdog: unknown service(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    attempts: dict[str, int] = {name: 0 for name in names}
    cooldown_until: dict[str, float] = {name: 0.0 for name in names}
    backoff = DEFAULT_BACKOFF

    log(f"watching {', '.join(names)} every {args.interval:g}s", verbose=True)
    while True:
        for name in names:
            now = time.time()
            if now < cooldown_until[name]:
                continue
            url = DEFAULT_PROBES[name]
            healthy, detail = probe(url, args.timeout)
            if healthy:
                if attempts[name] > 0:
                    log(f"{name} is healthy again after {attempts[name]} attempt(s)")
                    spool.write(
                        event(
                            "service_recovered",
                            name,
                            f"{name} answered {url} again after a restart",
                            level="warning",
                            context={"url": url, "attempts": attempts[name]},
                        )
                    )
                attempts[name] = 0
                continue

            attempts[name] += 1
            log(f"{name} is unhealthy ({detail}); attempt {attempts[name]}")
            spool.write(
                event(
                    "service_down",
                    name,
                    f"{name} did not answer {url}: {detail}",
                    level="critical",
                    context={
                        "url": url,
                        "detail": detail,
                        "attempt": attempts[name],
                        "log_tail": tail_log(log_dir / f"{name}.log"),
                    },
                )
            )

            if attempts[name] > args.max_attempts:
                log(f"{name} still down after {args.max_attempts} attempts; cooling down")
                spool.write(
                    event(
                        "service_down",
                        name,
                        f"{name} stayed down after {args.max_attempts} restart attempts",
                        level="critical",
                        context={
                            "url": url,
                            "detail": detail,
                            "exhausted": True,
                            "log_tail": tail_log(log_dir / f"{name}.log"),
                        },
                    )
                )
                attempts[name] = 0
                cooldown_until[name] = time.time() + args.cooldown
                continue

            started, output = restart(name, root)
            log(f"restart {name}: {'ok' if started else 'failed'}")
            spool.write(
                event(
                    "service_recovered" if started else "service_down",
                    name,
                    (
                        f"{name} restarted on attempt {attempts[name]}"
                        if started
                        else f"{name} restart failed on attempt {attempts[name]}"
                    ),
                    level="warning" if started else "critical",
                    context={
                        "url": url,
                        "attempt": attempts[name],
                        "output": output,
                        "log_tail": tail_log(log_dir / f"{name}.log"),
                    },
                )
            )
            if started:
                attempts[name] = 0
                continue
            # Give the service the configured backoff before trying again.
            wait = backoff[min(attempts[name] - 1, len(backoff) - 1)]
            log(f"waiting {wait}s before retrying {name}")
            time.sleep(wait)

        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("watchdog: stopped", file=sys.stderr)
        raise SystemExit(130)
