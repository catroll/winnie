"""Layout arithmetic: which file holds a week, and how files are named."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from winnie_schedule import layout
from winnie_schedule.layout import LayoutError

from .conftest import WEEK_TEXT, write_week


@pytest.mark.parametrize(
    ("month", "quarter"),
    [(1, 1), (3, 1), (4, 2), (6, 2), (7, 3), (9, 3), (10, 4), (12, 4)],
)
def test_quarter_of(month: int, quarter: int) -> None:
    assert layout.quarter_of(month) == quarter


def test_quarter_of_rejects_a_bad_month() -> None:
    with pytest.raises(LayoutError):
        layout.quarter_of(13)


@pytest.mark.parametrize(
    ("day", "stem"),
    [
        (date(2026, 10, 5), "202610-05-11"),
        (date(2026, 10, 11), "202610-05-11"),
        (date(2026, 10, 26), "202610-26-01"),
        (date(2026, 11, 1), "202610-26-01"),
        (date(2027, 1, 1), "202612-28-03"),
    ],
)
def test_week_stem_spans_the_right_days(day: date, stem: str) -> None:
    assert layout.week_of(day).stem == stem


def test_parse_stem_round_trips() -> None:
    weeks = layout.parse_stem("202610-26-01")
    assert weeks.monday == date(2026, 10, 26)
    assert weeks.sunday == date(2026, 11, 1)
    assert weeks.stem == "202610-26-01"
    assert [d.day for d in weeks.days] == [26, 27, 28, 29, 30, 31, 1]


@pytest.mark.parametrize("stem", ["202610-05-12", "202610-06-11", "202613-05-11", "202610-05", "junk"])
def test_parse_stem_rejects_names_that_are_not_a_week(stem: str) -> None:
    with pytest.raises(LayoutError):
        layout.parse_stem(stem)


@pytest.mark.parametrize(
    ("monday", "label"),
    [
        (date(2026, 10, 5), "2026-10A"),
        (date(2026, 10, 12), "2026-10B"),
        (date(2026, 10, 26), "2026-10D"),
        (date(2026, 11, 2), "2026-11A"),
    ],
)
def test_week_label_counts_mondays_in_the_month(monday: date, label: str) -> None:
    assert layout.week_label(monday) == label


def test_canonical_month_dir_carries_the_quarter() -> None:
    root = Path("/tmp/sched")
    assert layout.month_dir(root, date(2026, 10, 5)) == root / "work" / "2026" / "Q4" / "10"


def test_find_week_returns_the_monday_month_file(tmp_path: Path) -> None:
    root = tmp_path / "WLB"
    path = write_week(root, WEEK_TEXT)
    assert layout.find_week(root, date(2026, 10, 8)) == path
    assert layout.find_week(root, date(2026, 10, 5)) == path


def test_find_week_accepts_the_legacy_layout_without_a_quarter(tmp_path: Path) -> None:
    root = tmp_path / "WLB"
    legacy = root / "work" / "2019" / "04"
    legacy.mkdir(parents=True)
    path = legacy / "201904-01-07.md"
    path.write_text("# 周安排 01 - 07\n", encoding="utf-8")
    assert layout.find_week(root, date(2019, 4, 3)) == path


def test_find_week_falls_back_to_a_neighbour_month_directory(tmp_path: Path) -> None:
    root = tmp_path / "WLB"
    # Filed under November even though the week starts in October.
    wrong = root / "work" / "2026" / "Q4" / "11"
    wrong.mkdir(parents=True)
    path = wrong / "202610-26-01.md"
    path.write_text(WEEK_TEXT, encoding="utf-8")
    assert layout.find_week(root, date(2026, 10, 29)) == path


def test_find_week_explains_what_is_missing(tmp_path: Path) -> None:
    root = tmp_path / "WLB"
    (root / "work").mkdir(parents=True)
    with pytest.raises(LayoutError) as excinfo:
        layout.find_week(root, date(2026, 10, 5))
    assert "202610-05-11.md" in str(excinfo.value)


def test_related_files_reports_meeting_note_and_report(tmp_path: Path) -> None:
    root = tmp_path / "WLB"
    path = write_week(root, WEEK_TEXT)
    (path.parent / "202610-05-11-weekly.md").write_text("weekly\n", encoding="utf-8")
    (path.parent / "20261011-report.md").write_text("report\n", encoding="utf-8")
    related = layout.related_files(path)
    assert set(related) == {"weekly", "report"}


def test_week_files_skips_notes_and_weekly_records(tmp_path: Path) -> None:
    root = tmp_path / "WLB"
    path = write_week(root, WEEK_TEXT)
    (path.parent / "202610-05-11-weekly.md").write_text("weekly\n", encoding="utf-8")
    (path.parent / "20261005-notes.md").write_text("note\n", encoding="utf-8")
    assert layout.week_files(root) == [path]


def test_resolve_root_prefers_the_argument(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "WLB"
    (root / "work").mkdir(parents=True)
    monkeypatch.setenv(layout.ROOT_ENV, "/nonexistent")
    assert layout.resolve_root(root) == root.resolve()


def test_resolve_root_uses_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "WLB"
    (root / "work").mkdir(parents=True)
    monkeypatch.setenv(layout.ROOT_ENV, str(root))
    assert layout.resolve_root() == root.resolve()


def test_resolve_root_rejects_a_tree_without_work(tmp_path: Path) -> None:
    with pytest.raises(LayoutError):
        layout.resolve_root(tmp_path)
