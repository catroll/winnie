"""The data model of a parsed week file.

A week file is kept as its list of source lines. Everything else the parser
finds (day sections, events, tags) is a *view* with line ranges into that list,
so an edit can splice the exact lines it changes and leave every other byte of
the file alone. That property is what makes this tool safe to point at files a
human also edits by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from textwrap import dedent

from .layout import WeekRange

DAY_SCOPE = "day"
WEEK_SCOPE = "week"

LINK_RE = re.compile(r"(?<!\!)\[(?P<text>[^\]]*)\]\((?P<target>[^)\s]+)\)")
ANGLE_RE = re.compile(r"<(?P<target>[^>\s]+\.(?:md|txt|png|jpe?g|json|py|sh|ya?ml))>")


@dataclass(frozen=True)
class Link:
    """A reference from an event to a separate file, as written in Markdown."""

    text: str
    target: str

    def to_dict(self) -> dict[str, str]:
        return {"text": self.text, "target": self.target}


@dataclass
class Event:
    """One checkbox line, plus the indented lines that belong to it."""

    id: str
    scope: str
    day: date | None
    line_start: int
    line_end: int
    done: bool
    date_tag: str | None
    weekday_tag: str | None
    every: bool
    start: str | None
    end: str | None
    tags: tuple[str, ...]
    text: str
    indent: str
    bullet: str
    gap: int
    lines: tuple[str, ...]

    @property
    def time_range(self) -> str | None:
        if self.start and self.end:
            return f"{self.start}~{self.end}"
        return self.start

    @property
    def continuations(self) -> tuple[str, ...]:
        return self.lines[1:]

    @property
    def note(self) -> str:
        """The continuation block as Markdown, with its indent removed.

        The file indents those lines (4 to 6 spaces) to show that they belong
        to the checkbox above. That same indent is Markdown's code-block
        marker, so the raw block would render as preformatted text; removing
        the common indent gives back the paragraphs, lists and fences the
        author wrote. ``--note`` writes this block back with the indent.
        """
        return dedent("\n".join(self.lines[1:])).strip("\n")

    def matches(self, needle: str) -> bool:
        """Case-insensitive substring match over the whole event, text first."""
        haystacks = (self.text, "\n".join(self.lines))
        needle = needle.casefold()
        return any(needle in haystack.casefold() for haystack in haystacks)

    def to_dict(self, include_lines: bool = True) -> dict[str, object]:
        links = []
        for line in self.lines:
            links.extend(Link(text=m["text"], target=m["target"]) for m in LINK_RE.finditer(line))
            links.extend(Link(text="", target=m["target"]) for m in ANGLE_RE.finditer(line))
        payload: dict[str, object] = {
            "id": self.id,
            "scope": self.scope,
            "line": self.line_start + 1,
            "done": self.done,
            "date": self.date_tag,
            "weekday": self.weekday_tag,
            "every": self.every,
            "start": self.start,
            "end": self.end,
            "tags": list(self.tags),
            "text": self.text,
            "note": self.note,
            "links": [link.to_dict() for link in links],
        }
        if include_lines:
            payload["lines"] = list(self.lines)
        return payload


@dataclass
class Day:
    """A ``### MM/DD 周X`` section and the events directly under it."""

    date: date
    label: str
    heading: str
    line_start: int
    line_end: int
    events: list[Event] = field(default_factory=list)

    def to_dict(self, include_lines: bool = True) -> dict[str, object]:
        return {
            "date": self.date.isoformat(),
            "label": self.label,
            "heading": self.heading,
            "line": self.line_start + 1,
            "events": [event.to_dict(include_lines) for event in self.events],
        }


@dataclass
class Section:
    """A level-2 section such as ``## 工作事项`` or ``## 周末总结``."""

    level: int
    title: str
    line_start: int
    line_end: int
    events: list[Event] = field(default_factory=list)

    def to_dict(self, include_lines: bool = True) -> dict[str, object]:
        return {
            "title": self.title,
            "line": self.line_start + 1,
            "events": [event.to_dict(include_lines) for event in self.events],
        }


@dataclass
class Week:
    """A whole week file: its raw lines and the parsed view over them."""

    path: Path
    lines: list[str]
    weeks: WeekRange
    title: str
    final_newline: bool
    sections: list[Section] = field(default_factory=list)
    days: list[Day] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def layout(self) -> str:
        """``canonical`` when the file has ``### MM/DD`` day sections."""
        return "canonical" if self.days else "legacy"

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def editable(self) -> bool:
        """Only files with day sections can be edited without guessing."""
        return bool(self.days)

    def all_events(self, scope: str | None = None) -> list[Event]:
        events: list[Event] = []
        for section in self.sections:
            events.extend(section.events)
        for day in self.days:
            events.extend(day.events)
        if scope is None:
            return events
        return [event for event in events if event.scope == scope]

    def day(self, day: date) -> Day | None:
        for candidate in self.days:
            if candidate.date == day:
                return candidate
        return None

    def event(self, event_id: str) -> Event | None:
        for event in self.all_events():
            if event.id == event_id:
                return event
        return None

    def render(self) -> str:
        text = "\n".join(self.lines)
        if self.final_newline:
            text += "\n"
        return text

    def to_dict(self, include_lines: bool = True) -> dict[str, object]:
        from .layout import related_files

        return {
            "file": str(self.path),
            "stem": self.stem,
            "layout": self.layout,
            "editable": self.editable,
            "title": self.title,
            "week": self.weeks.to_dict(),
            "days": [day.to_dict(include_lines) for day in self.days],
            "sections": [section.to_dict(include_lines) for section in self.sections],
            "related": {name: str(path) for name, path in related_files(self.path).items()},
            "warnings": list(self.warnings),
        }
