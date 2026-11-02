"""One error, as every source reports it, and the key that deduplicates it.

A program error reaches this module through three very different paths: an
unhandled exception in the ASGI middleware, a ``logging`` record at ERROR from
any capability, and a JSON line the watchdog wrote while the API was down. All
three are normalised into :class:`ErrorEvent` so the rest of the feature never
has to care where an error came from.

The fingerprint is what turns a repeating failure into one row with a count. It
deliberately covers only the *shape* of the failure — kind, service, exception
type, source location and a masked message — because the alternative (hashing
the traceback, or the raw message) turns one bug into hundreds of rows. The
masking is intentionally narrow: UUIDs, hex identifiers and long numbers are
volatile; everything else is evidence.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Where an error was observed. `api` and `log` are in-process; `detector` is
# the health poller; `watchdog` arrives from the external supervisor's spool.
SOURCES = ("api", "log", "detector", "watchdog", "manual")

# What happened, which is not the same as where it was seen: an `api` source can
# produce an `unhandled_exception` or an `http_error`; a `watchdog` source can
# report `service_down` and `service_recovered`.
KINDS = (
    "unhandled_exception",
    "http_error",
    "logged_error",
    "service_unhealthy",
    "service_down",
    "service_recovered",
)

STATUSES = ("open", "acknowledged", "ignored", "resolved")
LEVELS = ("warning", "error", "critical")

_TERMINAL_STATUSES = ("ignored", "resolved")

_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
_LONG_NUMBER = re.compile(r"\b\d{3,}\b")
_WHITESPACE = re.compile(r"\s+")


def utcnow() -> datetime:
    """Naive UTC, as every other model in this repository stores time."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mask(message: str) -> str:
    """Remove the parts of a message that change between occurrences."""
    text = _UUID.sub("<uuid>", message or "")
    text = _HEX.sub("<hex>", text)
    text = _LONG_NUMBER.sub("<n>", text)
    return _WHITESPACE.sub(" ", text).strip()


@dataclass(slots=True)
class ErrorEvent:
    """A normalised failure, ready to be recorded."""

    message: str
    kind: str = "logged_error"
    source: str = "log"
    service: str = "api"
    level: str = "error"
    exception_type: str = ""
    location: str = ""
    traceback: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None

    def fingerprint(self) -> str:
        """A stable identity for one failure shape."""
        shape = "\x1f".join(
            (
                self.kind,
                self.service,
                self.exception_type,
                self.location,
                mask(self.message)[:200],
            )
        )
        return hashlib.sha256(shape.encode("utf-8")).hexdigest()[:32]

    def as_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "kind": self.kind,
            "source": self.source,
            "service": self.service,
            "level": self.level,
            "exception_type": self.exception_type,
            "location": self.location,
            "traceback": self.traceback,
            "context": self.context,
            "occurred_at": (self.occurred_at or utcnow()).isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ErrorEvent":
        """Build an event from a watchdog spool line or an API request body."""
        occurred_raw = str(payload.get("detected_at") or payload.get("occurred_at") or "")
        occurred = _parse_time(occurred_raw)
        source = str(payload.get("source") or "manual")
        if source not in SOURCES:
            source = "manual"
        level = str(payload.get("level") or "error")
        if level not in LEVELS:
            level = "error"
        context = payload.get("context")
        return cls(
            message=str(payload.get("message") or "")[:2000],
            kind=str(payload.get("kind") or "logged_error")[:48],
            source=source,
            service=str(payload.get("service") or "api")[:32],
            level=level,
            exception_type=str(payload.get("exception_type") or "")[:200],
            location=str(payload.get("location") or "")[:400],
            traceback=str(payload.get("traceback") or "")[:20000],
            context=context if isinstance(context, dict) else {},
            occurred_at=occurred,
        )


def _parse_time(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def coerce_status(value: str) -> str:
    status = (value or "").strip().lower()
    if status not in STATUSES:
        raise ValueError(f"unknown status: {value!r}")
    return status
