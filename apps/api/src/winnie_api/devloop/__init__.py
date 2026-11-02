"""Error collection.

Every surface in this package exists to answer one question — "what broke, how
often, and what does it mean" — without the owner grepping a log by hand.

- ``capture``: the ASGI middleware and root log handler that observe failures.
- ``detector``: the health poller and the watchdog spool drain.
- ``service``: the incident store (dedupe, triage, diagnosis, retention).
- ``evidence``/``diagnose``: the read-only bundle and the model call over it.
- ``router``: the administrator API the console reads.

Collection is a host concern, not a capability: it observes every package's
loggers and the whole ASGI app, and it must keep working when a capability is
switched off.
"""

from __future__ import annotations

from .config import DevloopConfig
from .events import ErrorEvent
from .service import ErrorService

__all__ = ["DevloopConfig", "ErrorEvent", "ErrorService"]
