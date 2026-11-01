"""Parse a week file into day sections and events.

The grammar the parser accepts is the one the WLB week generator produces and
the one every file since 2024-01 follows::

    # 周安排 05 - 11
    ```txt calendar block ```
    ## 工作事项                 <- week-scope events, no date tag
    -   [ ] **`项目名称`** ...
    ### 10/05 周一            <- day section; one per day of the week
    -   [ ] `10/05` [周一] [09:30~10:30] WoodyCoder 测试报告分析
        - nested line that belongs to the event above
    ## 周末总结                 <- prose, no events
    [badge]: https://...       <- link definitions, never touched

An event is one checkbox line plus every following line that is indented, or
blank and followed by an indented line. A blank line followed by a heading ends
the event, which is what keeps day sections separated. The raw block stays in
``Event.lines``; ``Event.note`` is the same block de-indented, which is what a
Markdown renderer wants (four spaces would be a code block).

Files from 2019-2023 have no day sections; they are parsed as ``legacy`` and
are read-only for this tool.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .layout import LayoutError, WeekRange, parse_stem, weekday_label
from .model import DAY_SCOPE, WEEK_SCOPE, Day, Event, Section, Week

HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<body>.*\S)\s*$")
DAY_HEADING_RE = re.compile(
    r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})\s+(?P<label>周[一二三四五六日天])(?P<rest>.*)$"
)
ITEM_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<bullet>[-*+])(?P<gap>[ \t]+)\[(?P<mark>[ xX])\][ \t]*(?P<body>.*)$"
)
DATE_TAG_RE = re.compile(r"^`(?P<month>\d{1,2})/(?P<day>\d{1,2})`")
TAG_RE = re.compile(r"^[ \t]*\[(?P<body>[^\]]*)\][ \t]*")
TIME_RANGE_RE = re.compile(
    r"^(?P<start>\d{1,2})[:.](?P<start_min>\d{2})[ \t]*[~-][ \t]*(?P<end>\d{1,2})[:.](?P<end_min>\d{2})$"
)
TIME_SINGLE_RE = re.compile(r"^(?P<start>\d{1,2})[:.](?P<start_min>\d{2})$")
WEEKDAY_TAG_RE = re.compile(r"^周[一二三四五六日天]$")

NORMALIZED_WEEKDAYS = {"周天": "周日"}


class ParseError(ValueError):
    """The file cannot be read as a week file."""


def normalize_weekday(label: str) -> str:
    return NORMALIZED_WEEKDAYS.get(label, label)


def parse_week(path: Path, text: str) -> Week:
    """Parse ``text`` (the contents of ``path``) into a :class:`Week`."""
    path = Path(path)
    try:
        weeks = parse_stem(path.stem)
    except LayoutError as exc:
        raise ParseError(f"{path.name}: {exc}") from exc
    if "\r" in text:
        raise ParseError(f"{path.name}: CRLF line endings are not supported")
    # Split on "\n" only. ``str.splitlines`` also breaks on form feed and
    # U+2028, and one file in the corpus contains a literal \x0b inside a
    # paragraph; splitting there would silently rewrite the file on save.
    final_newline = text.endswith("\n")
    lines = text.split("\n")
    if final_newline:
        lines.pop()
    week = Week(path=path, lines=lines, weeks=weeks, title="", final_newline=final_newline)

    section: Section | None = None
    container: Section | None = None  # the section items may belong to
    day: Day | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading["hashes"])
            body = heading["body"].strip()
            if level == 1:
                week.title = body
                section, day = _close(week, section, day, index)
                container = None
            elif level == 2:
                section, day = _close(week, section, day, index)
                section = Section(level=level, title=body, line_start=index, line_end=len(lines))
                container = section
                week.sections.append(section)
            elif level == 3:
                day_heading = DAY_HEADING_RE.match(body)
                if day_heading:
                    section, day = _close(week, section, day, index)
                    day = Day(
                        date=_resolve_day(week, day_heading, index),
                        label=normalize_weekday(day_heading["label"]),
                        heading=line,
                        line_start=index,
                        line_end=len(lines),
                    )
                    week.days.append(day)
                    _check_day_heading(week, day, day_heading, index)
                else:
                    # A level-3 heading inside a section (周末总结's subsections)
                    # ends the day view and stops items from belonging to the
                    # section.
                    section, day = _close(week, section, day, index)
                    container = None
            else:
                section, day = _close(week, section, day, index)
                container = None
            index += 1
            continue

        if item := _parse_item(line):
            end = event_end(lines, index)
            raw = tuple(lines[index:end])
            scope = DAY_SCOPE if day is not None else (WEEK_SCOPE if container is not None else None)
            if scope is None:
                index = end
                continue
            event = Event(
                id="",
                scope=scope,
                day=day.date if day is not None else None,
                line_start=index,
                line_end=end,
                done=item["done"],
                date_tag=item["date_tag"],
                weekday_tag=item["weekday_tag"],
                every=item["every"],
                start=item["start"],
                end=item["end"],
                tags=tuple(item["tags"]),
                text=item["text"],
                indent=item["indent"],
                bullet=item["bullet"],
                gap=item["gap"],
                lines=raw,
            )
            if day is not None:
                day.events.append(event)
                _check_event_date(week, day, event)
            else:
                assert container is not None
                container.events.append(event)
            index = end
            continue

        index += 1

    _close(week, section, day, len(lines))
    _assign_ids(week)
    return week


def parse_file(path: Path) -> Week:
    path = Path(path)
    return parse_week(path, path.read_text(encoding="utf-8"))


def _close(
    week: Week, section: Section | None, day: Day | None, index: int
) -> tuple[Section | None, Day | None]:
    if day is not None:
        day.line_end = index
    if section is not None:
        section.line_end = index
    return section, None


def _resolve_day(week: Week, heading: re.Match[str], index: int) -> date:
    month, day_of_month = int(heading["month"]), int(heading["day"])
    for candidate in week.weeks.days:
        if candidate.month == month and candidate.day == day_of_month:
            return candidate
    year = week.weeks.monday.year
    if month == 12 and week.weeks.monday.month == 1:
        year -= 1
    elif month == 1 and week.weeks.monday.month == 12:
        year += 1
    _warn(
        week,
        f"line {index + 1}: day heading {month:02d}/{day_of_month:02d} is outside "
        f"{week.weeks.monday.isoformat()} ~ {week.weeks.sunday.isoformat()}",
    )
    return date(year, month, day_of_month)


def _check_day_heading(week: Week, day: Day, heading: re.Match[str], index: int) -> None:
    expected = weekday_label(day.date)
    if day.label != expected:
        _warn(week, f"line {index + 1}: heading says {day.label} but {day.date.isoformat()} is {expected}")


def _check_event_date(week: Week, day: Day, event: Event) -> None:
    if event.date_tag is None:
        return
    if event.date_tag != f"{day.date:%m/%d}":
        _warn(
            week,
            f"line {event.line_start + 1}: event tag `{event.date_tag}` is in the "
            f"{day.date:%m/%d} section",
        )


def _warn(week: Week, message: str) -> None:
    week.warnings.append(message)


def _assign_ids(week: Week) -> None:
    counters: dict[str, int] = {}
    for section in week.sections:
        for event in section.events:
            counters["week"] = counters.get("week", 0) + 1
            event.id = f"{week.stem}#week#{counters['week']}"
    for day in week.days:
        key = f"{day.date:%m/%d}"
        for event in day.events:
            counters[key] = counters.get(key, 0) + 1
            event.id = f"{week.stem}#{key}#{counters[key]}"


def _parse_item(line: str) -> dict[str, object] | None:
    match = ITEM_RE.match(line)
    if not match:
        return None
    body = match["body"]
    date_tag: str | None = None
    if date_match := DATE_TAG_RE.match(body):
        date_tag = f"{int(date_match['month']):02d}/{int(date_match['day']):02d}"
        body = body[date_match.end():].lstrip()
    weekday_tag: str | None = None
    every = False
    start: str | None = None
    end: str | None = None
    tags: list[str] = []
    while tag := TAG_RE.match(body):
        value = tag["body"].strip()
        body = body[tag.end():]
        if time_range := TIME_RANGE_RE.match(value):
            start = f"{int(time_range['start']):02d}:{time_range['start_min']}"
            end = f"{int(time_range['end']):02d}:{time_range['end_min']}"
        elif time_single := TIME_SINGLE_RE.match(value):
            start = f"{int(time_single['start']):02d}:{time_single['start_min']}"
        elif WEEKDAY_TAG_RE.match(value):
            weekday_tag = normalize_weekday(value)
        elif value == "每天":
            every = True
        elif value:
            tags.append(value)
    return {
        "done": match["mark"] in "xX",
        "date_tag": date_tag,
        "weekday_tag": weekday_tag,
        "every": every,
        "start": start,
        "end": end,
        "tags": tags,
        "text": body.strip(),
        "indent": match["indent"],
        "bullet": match["bullet"],
        "gap": len(match["gap"]),
    }


def event_end(lines: list[str], start: int) -> int:
    """Return the first line after the event that begins at ``start``.

    An event owns every following indented line, plus blank runs that are
    followed by an indented line (a nested list or a fenced code block). A
    blank line followed by a heading or by a non-indented line ends it, and
    trailing blank lines are never part of the event.
    """
    index = start + 1
    last_content = start
    fence = False
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = not fence
            last_content = index
            index += 1
            continue
        if fence:
            last_content = index
            index += 1
            continue
        if not stripped:
            probe = index + 1
            while probe < len(lines) and not lines[probe].strip():
                probe += 1
            if probe < len(lines) and _is_continuation(lines[probe]) and not fence:
                index = probe
                continue
            break
        if not _is_continuation(line):
            break
        last_content = index
        index += 1
    return last_content + 1


def _is_continuation(line: str) -> bool:
    return line.startswith((" ", "\t"))


def new_event_id(week: Week, scope: str, day: date | None) -> str:
    """Return the id the next event of ``scope`` will receive."""
    if scope == WEEK_SCOPE:
        count = sum(len(section.events) for section in week.sections)
        return f"{week.stem}#week#{count + 1}"
    assert day is not None
    key = f"{day:%m/%d}"
    count = len([event for event in week.all_events() if event.scope == DAY_SCOPE and event.day == day])
    return f"{week.stem}#{key}#{count + 1}"
