"""Read-only checks against the real schedule tree.

These tests skip when the WLB checkout is not on this machine. They never write
to it: the mutation test copies one file into ``tmp_path`` first.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from winnie_schedule import layout
from winnie_schedule.editor import add_event, delete_event
from winnie_schedule.parser import parse_file

WLB = Path("~/Projects/Mine/WLB").expanduser()
needs_wlb = pytest.mark.skipif(
    not (WLB / "work").is_dir(), reason="the WLB checkout is not on this machine"
)


@pytest.fixture(scope="module")
def corpus() -> list[Path]:
    return layout.week_files(WLB)


@needs_wlb
def test_every_week_file_parses_and_round_trips(corpus: list[Path]) -> None:
    assert len(corpus) > 100
    for path in corpus:
        week = parse_file(path)
        assert week.render() == path.read_text(encoding="utf-8"), path
        assert week.weeks.monday.weekday() == 0


@needs_wlb
def test_the_corpus_has_both_layouts(corpus: list[Path]) -> None:
    layouts = {parse_file(path).layout for path in corpus}
    assert layouts == {"canonical", "legacy"}


@needs_wlb
def test_canonical_day_sections_are_an_ordered_subset_of_the_week(corpus: list[Path]) -> None:
    """One week in the corpus has six sections: a day was never added."""
    for path in corpus:
        week = parse_file(path)
        if week.layout != "canonical":
            continue
        dates = [day.date for day in week.days]
        assert dates == sorted(dates), path
        assert set(dates) <= set(week.weeks.days), path
        assert 6 <= len(dates) <= 7, path


@needs_wlb
def test_a_date_tag_that_contradicts_its_section_is_reported(corpus: list[Path]) -> None:
    """The corpus does contain such lines, and each one must be flagged.

    A missing tag is tolerated (2024 files write items without one), a
    contradicting tag is not: it means the line was pasted into the wrong day.
    """
    total = 0
    for path in corpus:
        week = parse_file(path)
        mismatches = sum(
            1
            for day in week.days
            for event in day.events
            if event.date_tag is not None and event.date_tag != f"{day.date:%m/%d}"
        )
        flagged = sum("event tag" in warning for warning in week.warnings)
        assert mismatches == flagged, path
        total += mismatches
    assert total > 0


@needs_wlb
def test_editing_a_real_week_leaves_it_unchanged(corpus: list[Path], tmp_path: Path) -> None:
    source = next(path for path in reversed(corpus) if parse_file(path).layout == "canonical")
    target = tmp_path / "work" / source.parent.parent.parent.name / source.parent.parent.name / source.parent.name
    target.mkdir(parents=True)
    copy = target / source.name
    shutil.copy2(source, copy)

    original = copy.read_text(encoding="utf-8")
    week = parse_file(copy)
    day = week.days[0].date
    added = add_event(week, text="临时验证事项", day=day, start="08:00", end="08:30", weekday=True)
    assert "临时验证事项" in copy.read_text(encoding="utf-8")
    delete_event(added.week, added.event.id)
    assert copy.read_text(encoding="utf-8") == original
