"""The parser: day sections, event grammar, tags and lossless round-tripping."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from winnie_schedule.model import DAY_SCOPE, WEEK_SCOPE
from winnie_schedule.parser import ParseError, parse_file, parse_week

from .conftest import WEEK_TEXT


def test_week_header_and_days(week) -> None:
    assert week.title == "周安排 05 - 11"
    assert week.weeks.monday == date(2026, 10, 5)
    assert week.layout == "canonical"
    assert [day.date.isoformat() for day in week.days] == [
        "2026-10-05",
        "2026-10-06",
        "2026-10-07",
        "2026-10-11",
    ]
    assert [day.label for day in week.days] == ["周一", "周二", "周三", "周日"]


def test_week_level_items_are_week_scoped(week) -> None:
    events = week.all_events(WEEK_SCOPE)
    assert [event.text for event in events] == ["**`项目名称`** 开发内容描述", "季度目标对齐全组"]
    assert all(event.date_tag is None for event in events)
    assert events[0].id == "202610-05-11#week#1"


def test_items_under_a_subsection_are_not_events(week) -> None:
    texts = [event.text for event in week.all_events()]
    assert "这不是事件" not in texts


def test_event_fields_and_tags(week) -> None:
    first = week.day(date(2026, 10, 5)).events[0]
    assert first.id == "202610-05-11#10/05#1"
    assert first.date_tag == "10/05"
    assert first.every is True
    assert first.time_range == "09:30~10:30"
    assert first.done is False
    assert first.text == "值班巡检（上午）"

    single = week.day(date(2026, 10, 6)).events[0]
    assert (single.start, single.end) == ("12:00", None)
    assert single.time_range == "12:00"

    tagged = week.day(date(2026, 10, 11)).events[0]
    assert tagged.tags == ("newapi",)
    assert tagged.text == "标签在日期之后"


def test_progress_state_is_read_from_the_checkbox(week) -> None:
    done = week.day(date(2026, 10, 5)).events[1]
    assert done.done is True
    assert done.text.startswith("已完成事项")


def test_nested_lines_belong_to_their_event(week) -> None:
    event = week.day(date(2026, 10, 5)).events[1]
    assert len(event.lines) == 3
    assert "子条目属于上一行" in event.lines[1]
    # The nested checkbox is content, not an event of its own.
    assert len(week.day(date(2026, 10, 5)).events) == 2


def test_a_fenced_block_belongs_to_its_event(week) -> None:
    event = week.day(date(2026, 10, 6)).events[1]
    assert len(event.lines) == 5
    assert any("echo hi" in line for line in event.lines)
    assert week.day(date(2026, 10, 6)).events[1].line_end == event.line_start + 5


def test_links_to_sibling_notes_are_extracted(week) -> None:
    event = week.day(date(2026, 10, 5)).events[1]
    links = event.to_dict()["links"]
    assert links == [{"text": "记录", "target": "20261005-notes.md"}]


def test_round_trip_is_byte_identical(week) -> None:
    assert week.render() == WEEK_TEXT


def test_parse_rejects_crlf(tmp_path: Path) -> None:
    path = tmp_path / "202610-05-11.md"
    with pytest.raises(ParseError):
        parse_week(path, "# 周安排 05 - 11\r\n")


def test_parse_rejects_a_name_that_is_not_a_week(tmp_path: Path) -> None:
    with pytest.raises(ParseError):
        parse_week(tmp_path / "20261005-notes.md", "# x\n")


def test_a_legacy_file_has_no_day_sections(tmp_path: Path) -> None:
    text = "# 周安排 01 - 07\n\n## 开发\n\n-   [x] **04/01** DNS 缓存问题\n"
    week = parse_week(tmp_path / "201904-01-07.md", text)
    assert week.layout == "legacy"
    assert week.editable is False
    assert [event.text for event in week.all_events()] == ["**04/01** DNS 缓存问题"]


def test_mismatched_event_date_produces_a_warning(tmp_path: Path) -> None:
    text = (
        "# 周安排 05 - 11\n\n"
        "### 10/05 周一\n\n"
        "-   [ ] `10/06` 记错日期的事项\n"
    )
    week = parse_week(tmp_path / "202610-05-11.md", text)
    assert any("`10/06`" in warning for warning in week.warnings)
    # The event still belongs to the section it was written in.
    assert week.day(date(2026, 10, 5)).events[0].day == date(2026, 10, 5)


def test_wrong_weekday_in_the_heading_produces_a_warning(tmp_path: Path) -> None:
    text = "# 周安排 05 - 11\n\n### 10/05 周三\n"
    week = parse_week(tmp_path / "202610-05-11.md", text)
    assert any("周三" in warning for warning in week.warnings)


def test_day_heading_outside_the_week_produces_a_warning(tmp_path: Path) -> None:
    text = "# 周安排 05 - 11\n\n### 11/20 周五\n"
    week = parse_week(tmp_path / "202610-05-11.md", text)
    assert week.days[0].date == date(2026, 11, 20)
    assert week.warnings


def test_an_unrelated_bullet_is_not_an_event(tmp_path: Path) -> None:
    text = "# 周安排 05 - 11\n\n## 周末总结\n\n- 一个普通列表项\n"
    week = parse_week(tmp_path / "202610-05-11.md", text)
    assert week.all_events() == []


def test_parse_file_matches_parse_week(week_path: Path) -> None:
    assert parse_file(week_path).render() == week_path.read_text(encoding="utf-8")
