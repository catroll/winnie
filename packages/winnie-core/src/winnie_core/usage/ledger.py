"""Append-only JSONL ledger for recorded model calls.

One file per UTC month under the configured root, one JSON object per line.
Appending a line is a single ``write(2)`` on a file opened with ``O_APPEND``, so
concurrent requests in one process cannot interleave halves of a record. A
failed write is logged and swallowed: telemetry must never break a model call.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from winnie_core.usage.event import UsageEvent

logger = logging.getLogger(__name__)


class UsageSink(Protocol):
    """Somewhere a usage event can be written; ``None`` means no recording."""

    def record(self, event: UsageEvent) -> None: ...


class UsageLedger:
    def __init__(self, root: Path, *, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self._lock = threading.Lock()

    def path_for(self, at: datetime) -> Path:
        moment = at.astimezone(timezone.utc)
        return self.root / f"usage-{moment:%Y-%m}.jsonl"

    def record(self, event: UsageEvent) -> None:
        if not self.enabled:
            return
        path = self.path_for(event.at)
        line = json.dumps(
            event.to_json(), ensure_ascii=False, separators=(",", ":")
        ) + "\n"
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
                )
                try:
                    os.write(descriptor, line.encode("utf-8"))
                finally:
                    os.close(descriptor)
        except OSError as exc:
            logger.warning("cannot append a usage record to %s: %s", path, exc)

    def read(self, *, start: datetime, end: datetime) -> list[UsageEvent]:
        """Events in ``[start, end]``, oldest first."""
        events: list[UsageEvent] = []
        for path in self._files(start, end):
            events.extend(
                event
                for event in self._read_file(path)
                if start <= event.at <= end
            )
        events.sort(key=lambda event: event.at)
        return events

    def _files(self, start: datetime, end: datetime) -> Iterable[Path]:
        for year, month in _months(start, end):
            path = self.root / f"usage-{year:04d}-{month:02d}.jsonl"
            if path.is_file():
                yield path

    def _read_file(self, path: Path) -> list[UsageEvent]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                lines = list(handle)
        except OSError as exc:
            logger.warning("cannot read the usage ledger at %s: %s", path, exc)
            return []

        events: list[UsageEvent] = []
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning("skipping malformed usage line %s:%s", path, number)
                continue
            if not isinstance(payload, dict):
                logger.warning("skipping non-object usage line %s:%s", path, number)
                continue
            try:
                events.append(UsageEvent.from_json(payload))
            except (TypeError, ValueError):
                logger.warning("skipping unreadable usage line %s:%s", path, number)
        return events


def _months(start: datetime, end: datetime) -> Iterable[tuple[int, int]]:
    first = start.astimezone(timezone.utc)
    last = end.astimezone(timezone.utc)
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        yield year, month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
