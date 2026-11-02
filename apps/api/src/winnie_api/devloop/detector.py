"""Periodic checks for the services the API does not own, and the spool drain.

The API cannot observe its own death, so process supervision belongs to
``scripts/watchdog.py``. This detector covers what a process *can* see: whether
the index daemon and the console are answering, whether the database still opens,
and the events the watchdog left behind while the API was down.

A recovered service closes its own incident. That is the difference between a
health poll and a status page: an incident that stays open after the next
successful probe is a false alarm the owner has to clear by hand.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from .events import ErrorEvent, utcnow
from .service import ErrorService

logger = logging.getLogger("winnie.devloop.detector")

# A spool file younger than this may still be being written by the watchdog.
_SPOOL_SETTLE_SECONDS = 2.0


class Detector:
    def __init__(
        self,
        service: ErrorService,
        *,
        service_urls: dict[str, str],
        spool_dir: Path,
        interval_seconds: float = 30.0,
        timeout_seconds: float = 3.0,
    ) -> None:
        self.service = service
        self.service_urls = dict(service_urls)
        self.spool_dir = spool_dir
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_purge: date | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        await asyncio.to_thread(self.spool_dir.mkdir, parents=True, exist_ok=True)
        self._stop.clear()
        self._task = asyncio.create_task(self.run(), name="winnie-devloop-detector")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.debug("devloop detector stopped with an error", exc_info=True)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the poller must survive one bad tick
                logger.exception("devloop detector tick failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.interval_seconds
                )
            except asyncio.TimeoutError:
                continue

    async def tick(self) -> None:
        await self.drain_spool()
        await self.probe_services()
        await self.maybe_purge()

    # -- watchdog spool -------------------------------------------------

    async def drain_spool(self) -> int:
        """Fold the watchdog's JSON lines into incidents, then delete them."""
        if not self.spool_dir.is_dir():
            return 0
        drained = 0
        # Epoch seconds, not `utcnow().timestamp()`: a naive UTC datetime is
        # interpreted in the host's local zone, which made every fresh spool file
        # look eight hours old on this machine (UTC+8) and skipped the settle
        # window in the wrong direction.
        now = time.time()
        for path in sorted(self.spool_dir.glob("*.jsonl")):
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            if age < _SPOOL_SETTLE_SECONDS:
                continue
            events = await asyncio.to_thread(_read_spool, path)
            if events:
                recovered = {event.service for event in events if event.kind == "service_recovered"}
                drained += await self.service.record_many(events)
                for service in recovered:
                    await self.service.auto_resolve(
                        service=service, kinds=("service_down", "service_unhealthy")
                    )
            try:
                path.unlink()
            except OSError:
                logger.debug("could not remove drained spool file %s", path)
        return drained

    # -- health ---------------------------------------------------------

    async def probe_services(self) -> None:
        if not self.service_urls:
            return
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for name, url in self.service_urls.items():
                await self._probe(client, name, url)

    async def _probe(self, client: httpx.AsyncClient, name: str, url: str) -> None:
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            await self.service.record(
                ErrorEvent(
                    message=f"health probe failed for {name}: {type(exc).__name__}",
                    kind="service_unhealthy",
                    source="detector",
                    service=name,
                    level="critical",
                    context={"url": url, "error": str(exc)[:500]},
                    occurred_at=utcnow(),
                )
            )
            return
        if response.status_code >= 400:
            await self.service.record(
                ErrorEvent(
                    message=f"health probe failed for {name}: HTTP {response.status_code}",
                    kind="service_unhealthy",
                    source="detector",
                    service=name,
                    level="critical",
                    context={"url": url, "status": response.status_code},
                    occurred_at=utcnow(),
                )
            )
            return
        await self.service.auto_resolve(service=name, kinds=("service_unhealthy",))

    # -- retention ------------------------------------------------------

    async def maybe_purge(self) -> None:
        today = utcnow().date()
        if self._last_purge == today:
            return
        self._last_purge = today
        try:
            removed = await self.service.purge()
        except Exception:  # noqa: BLE001 - retention is not worth failing a tick
            logger.exception("devloop retention sweep failed")
            return
        if removed:
            logger.info("pruned %d resolved error incidents", removed)


def _read_spool(path: Path) -> list[ErrorEvent]:
    events: list[ErrorEvent] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return events
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(ErrorEvent.from_dict(payload))
    return events
