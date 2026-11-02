"""Host wiring for error collection: build it, start it, stop it.

Kept out of ``main.py`` so the composition root stays a list of what is mounted
rather than a hundred lines of lifecycle detail. The middleware is registered in
``create_app`` because Starlette only accepts middleware before the app starts;
everything that needs a running loop is installed here from the lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from winnie_api.devloop import evidence as evidence_bundle
from winnie_api.devloop.capture import IncidentLogHandler, install_log_handler, remove_log_handler
from winnie_api.devloop.config import DevloopConfig
from winnie_api.devloop.detector import Detector
from winnie_api.devloop.service import ErrorService
from winnie_api.runtime import AppRuntime
from winnie_api.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class Devloop:
    service: ErrorService
    detector: Detector
    sources: evidence_bundle.EvidenceSources
    config: DevloopConfig
    handler: IncidentLogHandler | None = field(default=None)


def build_devloop(settings: Settings, runtime: AppRuntime) -> Devloop:
    config = settings.devloop
    service = ErrorService(
        runtime.session_factory, retention_days=config.retention_days
    )
    sources = evidence_bundle.EvidenceSources(
        repo_root=settings.resolve_repo_root(),
        log_dir=settings.resolve_devloop_log_dir(),
        service_urls=dict(config.service_urls),
    )
    detector = Detector(
        service,
        service_urls=dict(config.service_urls),
        spool_dir=settings.resolve_devloop_spool_dir(),
        interval_seconds=config.detector.interval_seconds,
        timeout_seconds=config.detector.probe_timeout_seconds,
    )
    return Devloop(service=service, detector=detector, sources=sources, config=config)


def chat_reader(model: Any) -> Any:
    """Adapt a ``ChatModel`` to the plain ``messages -> text`` the store wants."""

    async def chat(messages: list[dict[str, str]]) -> str:
        result = await model.chat(messages)
        return str(getattr(result, "text", "") or "")

    return chat


async def start_devloop(devloop: Devloop) -> None:
    loop = asyncio.get_running_loop()
    devloop.handler = install_log_handler(
        devloop.service,
        loop,
        repo_root=str(devloop.sources.repo_root),
    )
    if devloop.config.detector.enabled:
        await devloop.detector.start()
    logger.info(
        "collecting errors from the API and its loggers; detecting services=%s",
        ",".join(devloop.sources.service_urls) or "none",
    )


async def stop_devloop(devloop: Devloop) -> None:
    await devloop.detector.stop()
    if devloop.handler is not None:
        remove_log_handler(devloop.handler)
        devloop.handler = None
