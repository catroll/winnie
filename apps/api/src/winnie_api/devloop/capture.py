"""Capturing failures as they happen: request errors and log records.

Two seams are enough to see almost everything a running API does wrong:

- an ASGI middleware, which sees an unhandled exception and any 5xx response;
- a ``logging`` handler on the root logger, which sees what capabilities and
  background jobs report when no request owns the failure.

Both feed :class:`ErrorEvent` into the service. Both are defensive by design: a
collection failure must never become the error it was trying to record, so every
path swallows and logs at debug on a logger the handler itself ignores.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from pathlib import Path
from typing import Any

from .events import ErrorEvent, utcnow
from .service import ErrorService

logger = logging.getLogger("winnie.devloop")

# uvicorn reports the same ASGI exception the middleware already captured, and
# its access log is not an error source. Capturing them would double every 500.
_SKIPPED_LOGGERS = ("uvicorn", "winnie.devloop")


def repo_location(tb: traceback.TracebackException | None, repo_root: Path | str = "") -> str:
    """The innermost frame inside this checkout, as ``path:line``.

    Framework frames are useful to the model but useless as a fingerprint: they
    move with the dependency version. Application frames identify the bug.
    """
    if tb is None:
        return ""
    root = str(repo_root).rstrip("/") + "/" if repo_root else ""
    fallback = ""
    for frame in reversed(list(tb.stack)):
        where = f"{frame.filename}:{frame.lineno}"
        if not fallback:
            fallback = where
        if root and _is_application_frame(str(frame.filename), root):
            return where
    return fallback


# A virtualenv inside the checkout puts site-packages under the repo root, so a
# prefix test alone would pick fastapi's routing frame over the handler that
# raised. Installed code is not where a bug lives, and neither is the collection
# layer itself: every captured exception passes through this middleware, so its
# own frame would otherwise be the innermost application frame for all of them.
_NOT_APPLICATION = frozenset(
    {".venv", "site-packages", "node_modules", ".cache", ".git", "devloop"}
)


def _is_application_frame(filename: str, root: str) -> bool:
    if not filename.startswith(root):
        return False
    relative = filename[len(root) :]
    return not any(part in _NOT_APPLICATION for part in relative.split("/"))


def exception_event(
    exc: BaseException,
    *,
    source: str = "api",
    kind: str = "unhandled_exception",
    service: str = "api",
    level: str = "error",
    context: dict[str, Any] | None = None,
    repo_root: Path | str = "",
) -> ErrorEvent:
    """Normalise a caught exception into an event."""
    tb = traceback.TracebackException.from_exception(exc)
    return ErrorEvent(
        message=f"{type(exc).__name__}: {exc}"[:2000],
        kind=kind,
        source=source,
        service=service,
        level=level,
        exception_type=type(exc).__name__,
        location=repo_location(tb, repo_root),
        traceback="".join(tb.format())[:20000],
        context=context or {},
        occurred_at=utcnow(),
    )


async def safe_record(service: ErrorService | None, event: ErrorEvent) -> None:
    """Record, but never let the record fail the thing being recorded."""
    if service is None:
        return
    try:
        await service.record(event)
    except Exception:  # noqa: BLE001 - see the module docstring
        logger.debug("error collection failed", exc_info=True)


class ErrorCaptureMiddleware:
    """Record an unhandled exception, or a 5xx a handler chose to return."""

    def __init__(
        self,
        app,
        *,
        repo_root: str = "",
        capture_client_errors: bool = False,
    ) -> None:
        self.app = app
        self.repo_root = repo_root
        self.capture_client_errors = capture_client_errors

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        service: ErrorService | None = getattr(
            getattr(app, "state", None), "devloop_service", None
        )
        status = {"code": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = int(message.get("status", 0))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 - re-raised after recording
            if service is not None:
                request = scope.get("request")
                await safe_record(
                    service,
                    exception_event(
                        exc,
                        context=_request_context(scope, status="500"),
                        repo_root=self.repo_root,
                    ),
                )
            raise
        if status["code"] >= 500 and service is not None:
            await safe_record(
                service,
                ErrorEvent(
                    message=_http_message(scope, status["code"]),
                    kind="http_error",
                    source="api",
                    service="api",
                    level="error",
                    context=_request_context(scope, status=str(status["code"])),
                    occurred_at=utcnow(),
                ),
            )
        elif self.capture_client_errors and 400 <= status["code"] < 500 and service is not None:
            await safe_record(
                service,
                ErrorEvent(
                    message=_http_message(scope, status["code"]),
                    kind="http_error",
                    source="api",
                    service="api",
                    level="warning",
                    context=_request_context(scope, status=str(status["code"])),
                    occurred_at=utcnow(),
                ),
            )


def _http_message(scope, status: int) -> str:
    method = scope.get("method", "?")
    path = scope.get("path", "?")
    return f"{method} {path} -> {status}"


def _request_context(scope, *, status: str) -> dict[str, Any]:
    client = scope.get("client")
    return {
        "method": scope.get("method", ""),
        "path": scope.get("path", ""),
        "status": status,
        "client": client[0] if isinstance(client, tuple) and client else "",
    }


class IncidentLogHandler(logging.Handler):
    """Turn ERROR/CRITICAL records from any capability into incidents.

    A handler cannot await, and it may run on a worker thread (``to_thread``,
    ``run_in_executor``), so it hands the event to the loop the API installed it
    from and returns. The task it schedules is fire-and-forget; its own failure
    is swallowed on the devloop logger, which this handler ignores.
    """

    def __init__(
        self,
        service: ErrorService,
        loop: asyncio.AbstractEventLoop,
        *,
        repo_root: str = "",
        level: int = logging.ERROR,
    ) -> None:
        super().__init__(level=level)
        self.service = service
        self.loop = loop
        self.repo_root = repo_root

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith(_SKIPPED_LOGGERS):
                return
            message = record.getMessage()
            exc_info = record.exc_info
            event = ErrorEvent(
                message=message[:2000],
                kind="logged_error",
                source="log",
                service=_service_from_logger(record.name),
                level="critical" if record.levelno >= logging.CRITICAL else "error",
                exception_type=(
                    exc_info[0].__name__ if exc_info and exc_info[0] else ""
                ),
                location=f"{record.pathname}:{record.lineno}",
                traceback=(
                    "".join(traceback.format_exception(*exc_info))[:20000]
                    if exc_info
                    else ""
                ),
                context={"logger": record.name},
                occurred_at=utcnow(),
            )
        except Exception:  # noqa: BLE001 - a bad record must not break logging
            return
        try:
            self.loop.call_soon_threadsafe(self._schedule, event)
        except RuntimeError:
            # The loop is closed: shutdown is in progress and dropping the event
            # is the only safe answer.
            return

    def _schedule(self, event: ErrorEvent) -> None:
        async def run() -> None:
            await safe_record(self.service, event)

        try:
            task = asyncio.ensure_future(run())
        except RuntimeError:
            return
        task.add_done_callback(lambda _: None)


def _service_from_logger(name: str) -> str:
    """``winnie_mail.service`` -> ``mail``; anything else stays ``api``."""
    top = (name or "").split(".", 1)[0]
    if top.startswith("winnie_"):
        return top[len("winnie_") :][:32] or "api"
    return "api"


def install_log_handler(
    service: ErrorService,
    loop: asyncio.AbstractEventLoop,
    *,
    repo_root: str = "",
    level: int = logging.ERROR,
) -> IncidentLogHandler:
    handler = IncidentLogHandler(
        service, loop, repo_root=repo_root, level=level
    )
    root = logging.getLogger()
    root.addHandler(handler)
    return handler


def remove_log_handler(handler: IncidentLogHandler) -> None:
    logging.getLogger().removeHandler(handler)
    handler.close()
