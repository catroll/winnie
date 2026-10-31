"""Schedule reads and edits for the console.

The schedule tree belongs to the owner and lives outside this repository, so
this service is a thin translation layer: the file format, the parsing rules and
the line-splicing edits come from ``winnie-schedule``. What the host adds is the
configured root, a process-wide lock — an edit is a read-modify-write of one
file and two of them must not interleave — and the payload shape the console
consumes.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from winnie_schedule import editor, layout
from winnie_schedule.editor import ScheduleError
from winnie_schedule.layout import LayoutError
from winnie_schedule.model import Week
from winnie_schedule.parser import parse_file

MONTH_DAY_RE = re.compile(r"^(?P<month>\d{1,2})[/-](?P<day>\d{1,2})$")


class ScheduleUnavailable(RuntimeError):
    """The configured root is missing or is not a schedule tree."""


@dataclass
class ScheduleService:
    """One owner's weekly schedule tree."""

    root: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        if not (self.root / "work").is_dir():
            raise ScheduleUnavailable(f"{self.root} has no work/ directory")

    # ------------------------------------------------------------------ reads

    def week(self, day: date | None = None) -> dict[str, Any]:
        """Return the week that contains ``day`` (today when omitted)."""
        week = self._load(day or date.today())
        return self._payload(week)

    # ----------------------------------------------------------------- writes

    def add(
        self,
        *,
        day: date,
        text: str,
        start: str | None = None,
        end: str | None = None,
        weekday: bool = False,
        every: bool = False,
        tags: list[str] | None = None,
        done: bool = False,
        week_scope: bool = False,
        after: str | None = None,
    ) -> dict[str, Any]:
        """Insert one event into ``day``'s section, or into ``## 工作事项``."""
        with self._lock:
            week = self._load(day)
            result = editor.add_event(
                week,
                text=text,
                day=None if week_scope else day,
                start=start or None,
                end=end or None,
                weekday=weekday,
                every=every,
                tags=tuple(tags or ()),
                done=done,
                after=after,
            )
            return self._mutation(result)

    def update(
        self,
        *,
        week_date: date,
        event_id: str,
        text: str | None = None,
        time: str | None = None,
        done: bool | None = None,
        tags_add: list[str] | None = None,
        tags_remove: list[str] | None = None,
        note: str | None = None,
        move_to: str | None = None,
    ) -> dict[str, Any]:
        """Change one event; ``time`` of ``""`` clears the time tag."""
        with self._lock:
            week = self._load(week_date)
            start: object = editor.UNSET
            end: object = editor.UNSET
            if time is not None:
                start, end = ("", None) if not time.strip() else editor.normalize_time(time)
            result = editor.update_event(
                week,
                event_id,
                text=text if text is not None else editor.UNSET,
                start=start,
                end=end,
                done=done if done is not None else editor.UNSET,
                tags_add=tuple(tags_add or ()),
                tags_remove=tuple(tags_remove or ()),
                note=note,
                move_to=self._day_in_week(week, move_to) if move_to else None,
            )
            return self._mutation(result)

    def delete(self, *, week_date: date, event_id: str) -> dict[str, Any]:
        """Remove one event and the lines that belong to it."""
        with self._lock:
            week = self._load(week_date)
            result = editor.delete_event(week, event_id)
            payload = self._mutation(result)
            payload["removed"] = list(result.removed)
            return payload

    # ---------------------------------------------------------------- helpers

    def _load(self, day: date) -> Week:
        path = layout.find_week(self.root, day)
        return parse_file(path)

    def _payload(self, week: Week) -> dict[str, Any]:
        payload = week.to_dict(include_lines=False)
        payload["root"] = str(self.root)
        payload["today"] = date.today().isoformat()
        return payload

    def _mutation(self, result: editor.EditResult) -> dict[str, Any]:
        return {
            "changed": result.changed,
            "message": result.message,
            "event": result.event.to_dict() if result.event is not None else None,
            "week": self._payload(result.week),
        }

    def _day_in_week(self, week: Week, value: str) -> date:
        """Resolve ``MM/DD`` against the open week, or an ISO date."""
        match = MONTH_DAY_RE.match(value.strip())
        if match:
            month, day = int(match["month"]), int(match["day"])
            for candidate in week.weeks.days:
                if candidate.month == month and candidate.day == day:
                    return candidate
            raise ScheduleError(f"{value} is not one of the seven days of {week.stem}")
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ScheduleError(f"cannot read {value!r} as a day; use MM/DD or YYYY-MM-DD") from exc


__all__ = ["ScheduleService", "ScheduleUnavailable", "ScheduleError", "LayoutError"]
