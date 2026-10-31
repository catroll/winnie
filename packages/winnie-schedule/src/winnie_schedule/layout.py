"""Where a week lives in the schedule tree, and how to find it from a date.

The schedule is a directory tree with one Markdown file per week::

    <root>/work/<YYYY>/Q<quarter>/<MM>/<YYYYMM>-<DD>-<DD>.md

``<MM>`` is the month the **Monday** falls in, so the week of 2026-10-26 to
2026-11-01 is ``work/2026/Q4/10/202610-26-01.md``, not a file under ``11``.
The quarter is derived from that same month: ``(month + 2) // 3``.

The root defaults to ``~/Projects/Mine/WLB`` and can be overridden with
``--root`` or ``WINNIE_SCHEDULE_ROOT``.

Only layout arithmetic lives here. Reading and editing are in
:mod:`winnie_schedule.parser` and :mod:`winnie_schedule.editor`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

DEFAULT_ROOT = Path("~/Projects/Mine/WLB")
ROOT_ENV = "WINNIE_SCHEDULE_ROOT"

STEM_RE = re.compile(r"^(?P<year>\d{4})(?P<month>\d{2})-(?P<start>\d{2})-(?P<end>\d{2})$")

WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# The legacy generator numbers the weeks of a month A, B, C, ... for meeting
# notes; the label shows up in ``<stem>-weekly.md`` titles and TODO headings.
WEEK_LETTERS = "ABCDE"


class LayoutError(ValueError):
    """A date or file name does not describe a week in the schedule tree."""


@dataclass(frozen=True)
class WeekRange:
    """A Monday-to-Sunday week, as stored in one file."""

    monday: date

    @property
    def sunday(self) -> date:
        return self.monday + timedelta(days=6)

    @property
    def days(self) -> list[date]:
        return [self.monday + timedelta(days=offset) for offset in range(7)]

    @property
    def stem(self) -> str:
        return f"{self.monday:%Y%m}-{self.monday:%d}-{self.sunday:%d}"

    @property
    def label(self) -> str:
        return week_label(self.monday)

    def contains(self, day: date) -> bool:
        return self.monday <= day <= self.sunday

    def to_dict(self) -> dict[str, str]:
        return {
            "monday": self.monday.isoformat(),
            "sunday": self.sunday.isoformat(),
            "stem": self.stem,
            "label": self.label,
        }


def quarter_of(month: int) -> int:
    """Return the calendar quarter (1-4) of a month."""
    if not 1 <= month <= 12:
        raise LayoutError(f"month out of range: {month}")
    return (month + 2) // 3


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_of(day: date) -> WeekRange:
    return WeekRange(monday_of(day))


def week_label(monday: date) -> str:
    """Return the ``YYYY-MM<letter>`` label used by the weekly meeting note.

    The letter counts Mondays within the month, so 2026-10-05 is the first
    Monday of October 2026 and therefore ``2026-10A``.
    """
    monday = monday_of(monday)
    first = monday.replace(day=1)
    first_monday = first + timedelta(days=(7 - first.weekday()) % 7)
    index = (monday - first_monday).days // 7
    if not 0 <= index < len(WEEK_LETTERS):
        raise LayoutError(f"cannot label the week of {monday}: not a Monday week of its month")
    return f"{monday.year}-{monday.month:02d}{WEEK_LETTERS[index]}"


def month_dir(root: Path, day: date) -> Path:
    """Return the canonical month directory of ``day``: ``work/<YYYY>/Q<n>/<MM>``."""
    return Path(root) / "work" / f"{day.year:04d}" / f"Q{quarter_of(day.month)}" / f"{day.month:02d}"


def month_dirs(root: Path, day: date) -> list[Path]:
    """Return the month directory candidates of ``day``, canonical layout first.

    2019-2022 (and 2018) predate the ``Q<n>`` directory and keep their week
    files in ``work/<YYYY>/<MM>``, so reads must accept both.
    """
    canonical = month_dir(root, day)
    legacy = Path(root) / "work" / f"{day.year:04d}" / f"{day.month:02d}"
    if legacy == canonical:
        return [canonical]
    return [canonical, legacy]


def week_path(root: Path, day: date) -> Path:
    """Return the canonical path of the file that holds the week of ``day``."""
    weeks = week_of(day)
    return month_dir(root, weeks.monday) / f"{weeks.stem}.md"


def parse_stem(stem: str) -> WeekRange:
    """Parse ``202610-05-11`` into the week it names.

    Raises :class:`LayoutError` when the name is not a Monday-based week.
    """
    match = STEM_RE.match(stem)
    if not match:
        raise LayoutError(f"not a week file name: {stem!r}")
    year, month = int(match["year"]), int(match["month"])
    if not 1 <= month <= 12:
        raise LayoutError(f"month out of range in {stem!r}")
    try:
        monday = date(year, month, int(match["start"]))
    except ValueError as exc:
        raise LayoutError(f"{stem!r} has an impossible start day") from exc
    if monday.weekday() != 0:
        raise LayoutError(f"{stem!r} does not start on a Monday")
    weeks = WeekRange(monday)
    if weeks.stem != stem:
        raise LayoutError(f"{stem!r} imports a Sunday day that does not match {weeks.sunday:%d}")
    return weeks


def stem_of(path: Path) -> str:
    return Path(path).name.removesuffix(".md")


def find_week(root: Path, day: date) -> Path:
    """Return the week file covering ``day``, or raise :class:`LayoutError`.

    The canonical path is tried first. If it is missing, the month directories
    of the Monday and the Sunday are scanned in both the quarterly and the
    legacy layout, which keeps working for a week file filed by hand in the
    neighbouring month.
    """
    expected = week_path(root, day)
    if expected.exists():
        return expected
    weeks = week_of(day)
    seen: set[Path] = set()
    for probe in (weeks.monday, weeks.sunday):
        for directory in month_dirs(root, probe):
            if directory in seen or not directory.is_dir():
                continue
            seen.add(directory)
            for candidate in sorted(directory.glob("*.md")):
                try:
                    candidate_weeks = parse_stem(stem_of(candidate))
                except LayoutError:
                    continue
                if candidate_weeks.contains(day):
                    return candidate
    raise LayoutError(
        f"no week file covers {day.isoformat()}; expected {expected}. "
        "Create it with the WLB weekly generator (tools/weekly.py) first."
    )


def related_files(path: Path) -> dict[str, Path]:
    """Return the companion files that exist next to a week file.

    A week can be accompanied by its meeting note (``<stem>-weekly.md``) and by
    a work report named after the Sunday (``YYYYMMDD-report.md``).
    """
    path = Path(path)
    try:
        weeks = parse_stem(stem_of(path))
    except LayoutError:
        return {}
    candidates = {
        "weekly": path.with_name(f"{weeks.stem}-weekly.md"),
        "report": path.parent / f"{weeks.sunday:%Y%m%d}-report.md",
    }
    return {name: candidate for name, candidate in candidates.items() if candidate.exists()}


def week_files(root: Path) -> list[Path]:
    """Return every week file under ``<root>/work``, oldest first."""
    base = Path(root) / "work"
    if not base.is_dir():
        raise LayoutError(f"{base} is not a directory")
    found: list[Path] = []
    for path in base.rglob("*.md"):
        try:
            parse_stem(stem_of(path))
        except LayoutError:
            continue
        found.append(path)
    return sorted(found)


def resolve_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the schedule root: flag, then environment, then the default."""
    raw = explicit or os.environ.get(ROOT_ENV) or DEFAULT_ROOT
    root = Path(raw).expanduser().resolve()
    if not (root / "work").is_dir():
        raise LayoutError(f"{root} has no work/ directory; pass --root or set {ROOT_ENV}")
    return root


def human_range(weeks: WeekRange) -> str:
    """Return a compact human label such as ``2026-10-05 ~ 2026-10-11``."""
    return f"{weeks.monday.isoformat()} ~ {weeks.sunday.isoformat()}"


def weekday_label(day: date) -> str:
    return WEEKDAY_LABELS[day.weekday()]
