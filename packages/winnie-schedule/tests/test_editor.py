"""The editor: add, update and delete keep every unrelated byte in place."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from winnie_schedule import editor
from winnie_schedule.editor import (
    AmbiguousError,
    NotFoundError,
    ReadOnlyError,
    ScheduleError,
    add_event,
    delete_event,
    normalize_time,
    select_event,
    update_event,
)
from winnie_schedule.parser import parse_file

from .conftest import WEEK_TEXT, write_week

EMPTY_DAY_ADDED = WEEK_TEXT.replace(
    "### 10/07 周三\n\n### 10/11 周日\n",
    "### 10/07 周三\n\n-   [ ] `10/07` 新事项\n\n### 10/11 周日\n",
)


def test_add_appends_to_the_day(week, week_path: Path) -> None:
    result = add_event(week, text="新事项", day=date(2026, 10, 5))
    assert result.event.id == "202610-05-11#10/05#3"
    assert result.event.lines[0] == "-   [ ] `10/05` 新事项"
    # The new item follows the whole block, nested lines included.
    assert week_path.read_text(encoding="utf-8") == WEEK_TEXT.replace(
        "    -   [ ] 子条目里的复选框不是事件\n",
        "    -   [ ] 子条目里的复选框不是事件\n-   [ ] `10/05` 新事项\n",
    )


def test_add_to_an_empty_day_keeps_the_blank_line(week, week_path: Path) -> None:
    add_event(week, text="新事项", day=date(2026, 10, 7))
    assert week_path.read_text(encoding="utf-8") == EMPTY_DAY_ADDED


def test_add_at_top_goes_before_the_existing_events(week, week_path: Path) -> None:
    add_event(week, text="最前", day=date(2026, 10, 6), at_top=True)
    text = week_path.read_text(encoding="utf-8")
    first = text.index("-   [ ] `10/06` 最前")
    assert first < text.index("-   [ ] `10/06` [12:00] 单点时间")


def test_add_after_an_event(week) -> None:
    anchor = "202610-05-11#10/05#1"
    result = add_event(week, text="紧随其后", day=date(2026, 10, 5), after=anchor)
    assert result.event.id == "202610-05-11#10/05#2"
    day = result.week.day(date(2026, 10, 5))
    assert [event.text for event in day.events][:2] == ["值班巡检（上午）", "紧随其后"]


def test_add_with_time_tags_and_checkbox(week, week_path: Path) -> None:
    start, end = normalize_time("10:00~11:30")
    result = add_event(
        week,
        text="评审会",
        day=date(2026, 10, 6),
        start=start,
        end=end,
        weekday=True,
        tags=("newapi",),
        done=True,
    )
    assert result.event.lines[0] == "-   [x] `10/06` [周二] [10:00~11:30] [newapi] 评审会"


def test_add_week_scoped_item(week, week_path: Path) -> None:
    result = add_event(week, text="季度目标对齐全组", day=None)
    assert result.event.scope == "week"
    assert result.event.lines[0] == "-   [ ] 季度目标对齐全组"
    text = week_path.read_text(encoding="utf-8")
    assert text.index("季度目标对齐全组") < text.index("### 10/05 周一")


def test_add_multiline_text_indents_the_continuation(week) -> None:
    result = add_event(week, text="标题\n\n- 要点一\n- 要点二", day=date(2026, 10, 7))
    assert result.event.lines[0] == "-   [ ] `10/07` 标题"
    assert result.event.lines[1] == ""
    assert result.event.lines[2] == "      - 要点一"
    assert len(result.event.lines) == 4


def test_add_reuses_the_file_bullet_style(tmp_path: Path) -> None:
    root = tmp_path / "WLB"
    text = "# 周安排 05 - 11\n\n### 10/05 周一\n\n- [ ] `10/05` 旧事项\n"
    write_week(root, text)
    week = parse_file(root / "work/2026/Q4/10/202610-05-11.md")
    result = add_event(week, text="新事项", day=date(2026, 10, 5))
    assert result.event.lines[0] == "- [ ] `10/05` 新事项"


def test_add_refuses_a_missing_day(week) -> None:
    with pytest.raises(NotFoundError):
        add_event(week, text="x", day=date(2026, 10, 9))


def test_add_refuses_a_legacy_file(tmp_path: Path) -> None:
    root = tmp_path / "WLB"
    path = write_week(root, "# 周安排 01 - 07\n\n## 开发\n\n- [ ] 旧事项\n", monday=date(2019, 4, 1), name="201904-01-07")
    week = parse_file(path)
    with pytest.raises(ReadOnlyError):
        add_event(week, text="x", day=date(2019, 4, 1))


def test_update_text_keeps_the_prefix(week, week_path: Path) -> None:
    result = update_event(week, "202610-05-11#10/05#1", text="新的巡检描述")
    assert result.event.lines[0] == "-   [ ] `10/05` [每天] [09:30~10:30] 新的巡检描述"
    assert week_path.read_text(encoding="utf-8").count("新的巡检描述") == 1


def test_update_checks_and_unchecks(week) -> None:
    checked = update_event(week, "202610-05-11#10/05#1", done=True)
    assert checked.event.done is True
    unchecked = update_event(checked.week, "202610-05-11#10/05#1", done=False)
    assert unchecked.event.done is False


def test_update_sets_and_clears_the_time(week) -> None:
    updated = update_event(week, "202610-05-11#10/06#1", start="14:00", end="15:00")
    assert updated.event.time_range == "14:00~15:00"
    cleared = update_event(updated.week, "202610-05-11#10/06#1", start="", end=None)
    assert cleared.event.time_range is None
    assert cleared.event.lines[0] == "-   [ ] `10/06` 单点时间"


def test_update_adds_and_removes_tags(week) -> None:
    added = update_event(week, "202610-05-11#10/11#1", tags_add=("feat",))
    assert added.event.tags == ("newapi", "feat")
    removed = update_event(added.week, "202610-05-11#10/11#1", tags_remove=("newapi",))
    assert removed.event.tags == ("feat",)


def test_update_appends_a_note(week) -> None:
    result = update_event(week, "202610-05-11#10/06#1", note="补充说明\n第二行")
    assert result.event.lines[1] == "      补充说明"
    assert result.event.lines[2] == "      第二行"


def test_update_moves_the_event_and_fixes_the_weekday(week) -> None:
    result = update_event(week, "202610-05-11#10/05#1", move_to=date(2026, 10, 7))
    assert result.event.day == date(2026, 10, 7)
    # The event carries [每天], not a weekday tag, so none is invented.
    assert result.event.lines[0] == "-   [ ] `10/07` [每天] [09:30~10:30] 值班巡检（上午）"
    assert [event.text for event in result.week.day(date(2026, 10, 5)).events] == [
        "已完成事项 [记录](20261005-notes.md)"
    ]
    assert len(result.week.day(date(2026, 10, 7)).events) == 1


def test_update_move_preserves_nested_lines(week) -> None:
    result = update_event(week, "202610-05-11#10/05#2", move_to=date(2026, 10, 11))
    assert len(result.event.lines) == 3
    assert "子条目属于上一行" in result.event.lines[1]


def test_update_refuses_to_move_a_week_scoped_item(week) -> None:
    with pytest.raises(ScheduleError):
        update_event(week, "202610-05-11#week#1", move_to=date(2026, 10, 5))


def test_delete_removes_the_whole_block(week, week_path: Path) -> None:
    result = delete_event(week, "202610-05-11#10/05#2")
    assert len(result.removed) == 3
    assert "已完成事项" not in week_path.read_text(encoding="utf-8")
    # Only the block and its separator blank are gone.
    assert week_path.read_text(encoding="utf-8") == WEEK_TEXT.replace(
        "-   [x] `10/05` 已完成事项 [记录](20261005-notes.md)\n"
        "    -   子条目属于上一行\n"
        "    -   [ ] 子条目里的复选框不是事件\n",
        "",
    )


def test_delete_the_last_event_of_a_day_collapses_the_blank(week, week_path: Path) -> None:
    delete_event(week, "202610-05-11#10/11#1")
    text = week_path.read_text(encoding="utf-8")
    assert "### 10/11 周日\n\n## 周末总结" in text


def test_add_then_delete_restores_the_file(week) -> None:
    added = add_event(week, text="临时事项", day=date(2026, 10, 7))
    delete_event(added.week, added.event.id)
    assert added.path.read_text(encoding="utf-8") == WEEK_TEXT


def test_dry_run_changes_nothing(week, week_path: Path) -> None:
    before = week_path.read_bytes()
    result = add_event(week, text="预览", day=date(2026, 10, 5), dry_run=True)
    assert result.dry_run is True
    assert result.changed is True
    assert "预览" in result.diff
    assert week_path.read_bytes() == before


def test_write_preserves_the_file_mode(week, week_path: Path) -> None:
    os.chmod(week_path, 0o640)
    add_event(week, text="权限", day=date(2026, 10, 5))
    assert week_path.stat().st_mode & 0o777 == 0o640


def test_no_change_leaves_the_file_alone(week, week_path: Path) -> None:
    before = week_path.stat().st_mtime_ns
    result = update_event(week, "202610-05-11#10/05#1", done=False)
    assert result.changed is False
    assert week_path.stat().st_mtime_ns == before


def test_select_event_by_match(week) -> None:
    event = select_event(week, match="季度目标")
    assert event.id == "202610-05-11#week#2"


def test_select_event_rejects_an_ambiguous_match(tmp_path: Path) -> None:
    root = tmp_path / "WLB"
    text = "# 周安排 05 - 11\n\n### 10/05 周一\n\n-   [ ] `10/05` 重复事项\n-   [ ] `10/05` 重复事项\n"
    path = write_week(root, text)
    with pytest.raises(AmbiguousError) as excinfo:
        select_event(parse_file(path), match="重复事项")
    assert "#10/05#1" in str(excinfo.value)


def test_select_event_rejects_a_missing_match(week) -> None:
    with pytest.raises(NotFoundError):
        select_event(week, match="不存在的文字")


def test_select_event_restricts_to_a_day(week) -> None:
    event = select_event(week, match="单点时间", day=date(2026, 10, 6))
    assert event.text == "单点时间"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("09:30", ("09:30", None)),
        ("9:30~10:30", ("09:30", "10:30")),
        ("09:30-10:30", ("09:30", "10:30")),
        ("", ("", None)),
    ],
)
def test_normalize_time(value: str, expected: tuple[str, str | None]) -> None:
    assert normalize_time(value) == expected


@pytest.mark.parametrize("value", ["9", "09:30~", "25:00", "10:00~09:00"])
def test_normalize_time_rejects_bad_input(value: str) -> None:
    with pytest.raises(ScheduleError):
        normalize_time(value)


def test_editor_does_not_touch_the_calendar_block(week) -> None:
    result = add_event(week, text="x", day=date(2026, 10, 5))
    assert result.after.startswith("# 周安排 05 - 11\n\n```txt\n")


def test_format_event_line_orders_the_tags() -> None:
    line = editor.format_event_line(
        bullet="-",
        gap=3,
        done=False,
        date_tag="10/05",
        every=True,
        weekday="周一",
        start="09:30",
        end="10:30",
        tags=("newapi",),
        text="标题",
    )
    assert line == "-   [ ] `10/05` [每天] [周一] [09:30~10:30] [newapi] 标题"
