"""Add, update and delete events in a week file without rewriting it.

Every mutation is a splice on the file's line list: the changed line is
rebuilt from the parsed fields, and inserted or removed lines are the only
difference in the output. Nothing is re-serialised from the model, so a file
that a human also edits keeps its prose, its blank lines, its escaped
characters and its badge definitions exactly as they were.

The one thing an edit must get right is the event *prefix*::

    -   [ ] `10/05` [周一] [09:30~10:30] WoodyCoder 测试报告分析

where the parts are, in canonical order, the checkbox, the ``MM/DD`` tag, the
``[每天]`` marker, the weekday tag, the time range and any remaining tags. The
original indentation, bullet character and spacing after the bullet are reused,
so a file written as ``- [ ]`` does not gain ``-   [ ]`` lines.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import date
from difflib import unified_diff
from pathlib import Path

from .layout import WEEKDAY_LABELS
from .model import DAY_SCOPE, WEEK_SCOPE, Event, Section, Week
from .parser import parse_file, parse_week

TIME_RE = re.compile(r"^(?P<start>\d{1,2}):(?P<start_min>\d{2})(?:[~-](?P<end>\d{1,2}):(?P<end_min>\d{2}))?$")
WEEK_ITEM_SECTION = "工作事项"
CONTINUATION_INDENT = " " * 6
UNSET: object = object()


class ScheduleError(Exception):
    """Base class for edit failures the command line reports."""

    exit_code = 1


class NotFoundError(ScheduleError):
    """The file, day or event asked for does not exist."""

    exit_code = 2


class AmbiguousError(ScheduleError):
    """A text match hit more than one event."""

    exit_code = 3


class ReadOnlyError(ScheduleError):
    """The file has no day sections, so it cannot be edited safely."""

    exit_code = 4


@dataclass
class EditResult:
    """What one mutation did, or would do under ``--dry-run``."""

    path: Path
    before: str
    after: str
    week: Week
    event: Event | None = None
    removed: tuple[str, ...] = ()
    message: str = ""
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        return self.before != self.after

    @property
    def diff(self) -> str:
        return "".join(
            unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=f"a/{self.path.name}",
                tofile=f"b/{self.path.name}",
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "file": str(self.path),
            "changed": self.changed,
            "dry_run": self.dry_run,
            "message": self.message,
            "diff": self.diff,
            "event": self.event.to_dict() if self.event else None,
            "removed": list(self.removed),
        }


# --------------------------------------------------------------------------- #
# Rendering one event line
# --------------------------------------------------------------------------- #


def normalize_time(value: str) -> tuple[str, str | None]:
    """Return ``(start, end)`` for ``09:30`` or ``09:30~10:30``; empty clears it."""
    value = value.strip()
    if not value:
        return "", None
    match = TIME_RE.match(value)
    if not match:
        raise ScheduleError(f"bad time {value!r}; use HH:MM or HH:MM~HH:MM")
    start = _clock(int(match["start"]), int(match["start_min"]), value)
    end = None
    if match["end"]:
        end = _clock(int(match["end"]), int(match["end_min"]), value)
        if end <= start:
            raise ScheduleError(f"time range {value!r} ends before it starts")
    return start, end


def _clock(hour: int, minute: int, source: str) -> str:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ScheduleError(f"time {source!r} is not a valid time of day")
    return f"{hour:02d}:{minute:02d}"


def format_event_line(
    *,
    bullet: str,
    gap: int,
    done: bool,
    date_tag: str | None,
    every: bool = False,
    weekday: str | None = None,
    start: str | None = None,
    end: str | None = None,
    tags: tuple[str, ...] | list[str] = (),
    text: str = "",
) -> str:
    """Render the first line of an event, in the order the corpus uses."""
    parts: list[str] = []
    if date_tag:
        parts.append(f"`{date_tag}`")
    if every:
        parts.append("[每天]")
    if weekday:
        parts.append(f"[{weekday}]")
    if start:
        parts.append(f"[{start}~{end}]" if end else f"[{start}]")
    parts.extend(f"[{tag}]" for tag in tags)
    if text:
        parts.append(text)
    head = f"{bullet}{' ' * gap}[{'x' if done else ' '}]"
    return f"{head} {' '.join(parts)}" if parts else head


def _split_text(text: str) -> list[str]:
    """Split user text into the event line and indented continuation lines."""
    lines = text.split("\n")
    return [lines[0].strip()] + [f"{CONTINUATION_INDENT}{line.rstrip()}" if line.strip() else "" for line in lines[1:]]


def _weekday_of(day: date) -> str:
    return WEEKDAY_LABELS[day.weekday()]


# --------------------------------------------------------------------------- #
# Choosing an event
# --------------------------------------------------------------------------- #


def select_event(
    week: Week,
    event_id: str | None = None,
    match: str | None = None,
    day: date | None = None,
    scope: str | None = None,
) -> Event:
    """Resolve exactly one event by id or by unique text match."""
    if event_id and match:
        raise ScheduleError("pass either an event id or --match, not both")
    if not event_id and not match:
        raise ScheduleError("pass an event id or --match")
    if event_id:
        event = week.event(event_id)
        if event is None:
            raise NotFoundError(f"no event {event_id} in {week.path}")
        return event

    assert match is not None
    candidates = week.all_events()
    if scope:
        candidates = [event for event in candidates if event.scope == scope]
    if day is not None:
        candidates = [event for event in candidates if event.scope == DAY_SCOPE and event.day == day]
    hits = [event for event in candidates if event.matches(match)]
    if not hits:
        raise NotFoundError(f"no event matching {match!r} in {week.path}")
    if len(hits) > 1:
        listing = "\n".join(f"  {event.id}  {event.text}" for event in hits[:10])
        raise AmbiguousError(f"{len(hits)} events match {match!r}; narrow the search or use an id:\n{listing}")
    return hits[0]


# --------------------------------------------------------------------------- #
# Insertion points
# --------------------------------------------------------------------------- #


def _section(week: Week, title: str) -> Section | None:
    for section in week.sections:
        if section.title == title:
            return section
    return None


def _block_end(week: Week, events: list[Event], heading_line: int | None) -> int:
    """Return the line index just after the last event, or after the heading.

    An empty block inserts one line below the heading, which is where the
    blank line that follows a heading lives.
    """
    if events:
        return events[-1].line_end
    if heading_line is None:
        return len(week.lines)
    if heading_line + 1 < len(week.lines) and not week.lines[heading_line + 1].strip():
        return heading_line + 2
    return heading_line + 1


def _next_line_ends_block(week: Week, index: int) -> bool:
    """True when ``index`` is the end of a section rather than between items."""
    if index >= len(week.lines):
        return True
    line = week.lines[index]
    return not line.strip() or line.lstrip().startswith("#")


def _insert(week: Week, index: int, new_lines: list[str], *, at_block_end: bool) -> int:
    block = list(new_lines)
    if at_block_end and _next_line_ends_block(week, index):
        if index >= len(week.lines) or week.lines[index].strip():
            block.append("")
    for offset, line in enumerate(block):
        week.lines.insert(index + offset, line)
    if index + len(block) >= len(week.lines):
        week.final_newline = True
    return index


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #


def _style(week: Week) -> tuple[str, int]:
    """Reuse the bullet character and spacing of the file's own events."""
    for day in week.days:
        for event in day.events:
            return event.bullet, event.gap
    for section in week.sections:
        for event in section.events:
            return event.bullet, event.gap
    return "-", 3


def _require_editable(week: Week) -> None:
    if not week.editable:
        raise ReadOnlyError(
            f"{week.path.name} has no '### MM/DD 周X' day sections; "
            "files before 2024 are read-only for this tool"
        )


def _day_or_fail(week: Week, day: date):
    found = week.day(day)
    if found is None:
        available = ", ".join(f"{candidate.date:%m/%d}" for candidate in week.days) or "none"
        raise NotFoundError(
            f"{day:%m/%d} is not a day section of {week.path.name} "
            f"({week.weeks.monday.isoformat()} ~ {week.weeks.sunday.isoformat()}); have: {available}"
        )
    return found


def add_event(
    week: Week,
    *,
    text: str,
    day: date | None = None,
    start: str | None = None,
    end: str | None = None,
    weekday: bool = False,
    every: bool = False,
    tags: tuple[str, ...] = (),
    done: bool = False,
    after: str | None = None,
    at_top: bool = False,
    dry_run: bool = False,
) -> EditResult:
    """Insert one event into ``day``'s section, or into ``## 工作事项``.

    With ``day`` the event goes into that day section and carries the
    ``MM/DD`` tag; without it the event is week-scoped and goes into
    ``## 工作事项``. ``after`` is an event id in the same block; otherwise the
    event is appended to the block unless ``at_top`` is set.
    """
    _require_editable(week)
    before = week.render()
    bullet, gap = _style(week)

    if day is None:
        section = _section(week, WEEK_ITEM_SECTION)
        if section is None:
            raise NotFoundError(f"{week.path.name} has no '## {WEEK_ITEM_SECTION}' section")
        events = list(section.events)
        heading_line = section.line_start
        date_tag = None
        weekday_tag = None
    else:
        target = _day_or_fail(week, day)
        events = list(target.events)
        heading_line = target.line_start
        date_tag = f"{day:%m/%d}"
        weekday_tag = _weekday_of(day) if weekday else None

    rendered = format_event_line(
        bullet=bullet,
        gap=gap,
        done=done,
        date_tag=date_tag,
        every=every,
        weekday=weekday_tag,
        start=start,
        end=end,
        tags=tags,
        text=text,
    )
    new_lines = _split_text(rendered)
    if after is not None:
        anchor = week.event(after)
        if anchor is None:
            raise NotFoundError(f"no event {after} to insert after")
        if day is not None and anchor.day != day:
            raise ScheduleError(f"event {after} is not in the {day:%m/%d} section")
        index = anchor.line_end
        at_block_end = False
    elif at_top and events:
        index = events[0].line_start
        at_block_end = False
    else:
        index = events[0].line_start if at_top else _block_end(week, events, heading_line)
        at_block_end = True
    index = _insert(week, index, new_lines, at_block_end=at_block_end)

    return _finish(week, before, dry_run=dry_run, line=index, message=f"added event to {_where(day)}")


def _where(day: date | None) -> str:
    return f"{day:%m/%d}" if day else WEEK_ITEM_SECTION


def update_event(
    week: Week,
    event_id: str,
    *,
    text: object = UNSET,
    start: object = UNSET,
    end: object = UNSET,
    every: object = UNSET,
    weekday: object = UNSET,
    tags_add: tuple[str, ...] = (),
    tags_remove: tuple[str, ...] = (),
    done: object = UNSET,
    note: str | None = None,
    move_to: date | None = None,
    dry_run: bool = False,
) -> EditResult:
    """Change fields of one event, keeping its position and nested content.

    Only the event's first line is rebuilt; continuation lines are untouched.
    ``--note`` appends indented lines after the event's last line.
    """
    _require_editable(week)
    before = week.render()
    event = select_event(week, event_id)
    target_day = event.day
    if move_to is not None:
        if event.scope != DAY_SCOPE or event.day is None:
            raise ScheduleError("a week-scoped event cannot be moved to a day; change its scope by deleting and re-adding it")
        _day_or_fail(week, move_to)
        target_day = move_to

    new_start = event.start if start is UNSET else (start or None)
    new_end = event.end if end is UNSET else (end or None)
    if new_start is None:
        new_end = None
    new_weekday = event.weekday_tag if weekday is UNSET else (weekday or None)
    if target_day is not None and event.scope == DAY_SCOPE and new_weekday:
        # Keep the weekday tag truthful when the event moves days.
        new_weekday = _weekday_of(target_day)
    new_every = event.every if every is UNSET else bool(every)
    new_done = event.done if done is UNSET else bool(done)
    new_text = event.text if text is UNSET else str(text)
    new_tags = [tag for tag in event.tags if tag not in tags_remove]
    for tag in tags_add:
        if tag not in new_tags:
            new_tags.append(tag)
    # A day event without a date tag stays without one; only a move - or an
    # event that already carries the tag - writes the day into the line.
    if target_day is not None and event.scope == DAY_SCOPE and (event.date_tag or move_to is not None):
        date_tag: str | None = f"{target_day:%m/%d}"
    else:
        date_tag = event.date_tag

    rebuilt = format_event_line(
        bullet=event.bullet,
        gap=event.gap,
        done=new_done,
        date_tag=date_tag,
        every=new_every,
        weekday=new_weekday,
        start=new_start,
        end=new_end,
        tags=tuple(new_tags),
        text=new_text,
    )
    block = list(event.lines)
    block[0] = rebuilt
    if note:
        block.extend(_note_lines(note))

    if move_to is not None and event.day != move_to:
        del week.lines[event.line_start:event.line_end]
        _remove_trailing_blank(week, event.line_start)
        # Positions of the following sections shifted with the deletion, so the
        # target is located on a fresh parse of the shortened text.
        week = parse_week(week.path, week.render())
        target = _day_or_fail(week, move_to)
        index = _block_end(week, target.events, target.line_start)
        index = _insert(week, index, block, at_block_end=True)
        message = f"moved event to {move_to:%m/%d}"
    else:
        week.lines[event.line_start:event.line_end] = block
        index = event.line_start
        message = "updated event"

    return _finish(week, before, dry_run=dry_run, line=index, message=message)


def _note_lines(note: str) -> list[str]:
    return [f"{CONTINUATION_INDENT}{line.rstrip()}" if line.strip() else "" for line in note.split("\n")]


def delete_event(week: Week, event_id: str, *, dry_run: bool = False) -> EditResult:
    """Remove an event and the lines that belong to it."""
    _require_editable(week)
    before = week.render()
    event = select_event(week, event_id)
    removed = event.lines
    del week.lines[event.line_start:event.line_end]
    _remove_trailing_blank(week, event.line_start)
    return _finish(week, before, dry_run=dry_run, line=None, message="deleted event", removed=removed)


def _remove_trailing_blank(week: Week, index: int) -> None:
    """Collapse the double blank line a deletion can leave behind."""
    if index <= 0 or index >= len(week.lines):
        return
    if week.lines[index - 1].strip() or week.lines[index].strip():
        return
    del week.lines[index]


def _finish(
    week: Week,
    before: str,
    *,
    dry_run: bool,
    line: int | None,
    message: str,
    removed: tuple[str, ...] = (),
) -> EditResult:
    after = week.render()
    if dry_run or after == before:
        reparsed = parse_week(week.path, after)
    else:
        _write(week.path, after)
        reparsed = parse_file(week.path)
    event = None
    if line is not None and line < len(reparsed.lines):
        for candidate in reparsed.all_events():
            if candidate.line_start == line:
                event = candidate
                break
    return EditResult(
        path=week.path,
        before=before,
        after=after,
        week=reparsed,
        event=event,
        removed=removed,
        message=message,
        dry_run=dry_run,
    )


def _write(path: Path, text: str) -> None:
    """Replace the file atomically, keeping its mode."""
    mode = stat.S_IMODE(path.stat().st_mode)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
