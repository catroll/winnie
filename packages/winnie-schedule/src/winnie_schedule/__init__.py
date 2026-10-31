"""Read and edit the weekly schedule kept in the WLB ``work/`` tree.

The public surface is small on purpose:

- :func:`winnie_schedule.load` returns the :class:`~winnie_schedule.model.Week`
  that owns a date, parsed from disk.
- :func:`winnie_schedule.add_event`, :func:`~winnie_schedule.update_event` and
  :func:`~winnie_schedule.delete_event` splice lines into that file and keep
  every other byte untouched.
- ``python -m winnie_schedule`` is the command line over the same functions.

Week files stay the source of truth; this package never keeps its own copy of
the schedule.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .editor import EditResult, ScheduleError, add_event, delete_event, update_event
from .layout import LayoutError, WeekRange, find_week, related_files, resolve_root, week_of, week_path
from .model import DAY_SCOPE, WEEK_SCOPE, Day, Event, Link, Section, Week
from .parser import ParseError, parse_file, parse_week

__all__ = [
    "DAY_SCOPE",
    "WEEK_SCOPE",
    "Day",
    "EditResult",
    "Event",
    "LayoutError",
    "Link",
    "ParseError",
    "ScheduleError",
    "Section",
    "Week",
    "WeekRange",
    "add_event",
    "delete_event",
    "find_week",
    "load",
    "parse_file",
    "parse_week",
    "related_files",
    "resolve_root",
    "update_event",
    "week_of",
    "week_path",
]


def load(root: Path | str, day: date) -> Week:
    """Load the week file that covers ``day`` below ``root``."""
    return parse_file(find_week(Path(root), day))
