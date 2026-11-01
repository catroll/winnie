"""The command line: output shapes, exit codes and JSON."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from winnie_schedule.cli import main, parse_day
from winnie_schedule.parser import parse_file

from .conftest import WEEK_TEXT, write_week


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_show_lists_days_and_events(schedule_root: Path, capsys) -> None:
    code, out, _ = run(capsys, "--root", str(schedule_root), "show", "2026-10-05")
    assert code == 0
    assert "2026-10-05 ~ 2026-10-11" in out
    assert "10/05 周一" in out
    assert "值班巡检（上午）" in out


def test_show_can_limit_to_one_day(schedule_root: Path, capsys) -> None:
    code, out, _ = run(capsys, "--root", str(schedule_root), "show", "2026-10-05", "--day")
    assert code == 0
    assert "10/05 周一" in out
    assert "10/06 周二" not in out


def test_parse_emits_json(schedule_root: Path, capsys) -> None:
    code, out, _ = run(capsys, "--root", str(schedule_root), "parse", "2026-10-05")
    assert code == 0
    payload = json.loads(out)
    assert payload["stem"] == "202610-05-11"
    assert payload["week"]["label"] == "2026-10A"
    assert payload["days"][0]["events"][0]["start"] == "09:30"
    assert payload["days"][0]["events"][1]["links"][0]["target"] == "20261005-notes.md"


def test_list_filters_unchecked_events(schedule_root: Path, capsys) -> None:
    code, out, _ = run(capsys, "--root", str(schedule_root), "list", "2026-10-05", "--pending")
    assert code == 0
    assert "已完成事项" not in out


def test_add_then_list(schedule_root: Path, capsys) -> None:
    code, out, _ = run(
        capsys,
        "--root",
        str(schedule_root),
        "add",
        "2026-10-07",
        "CLI 新事项",
        "--time",
        "14:00~15:00",
        "--weekday",
    )
    assert code == 0
    assert "10/07#1" in out
    code, out, _ = run(capsys, "--root", str(schedule_root), "list", "2026-10-07", "--day-only")
    assert "14:00~15:00" in out
    assert "CLI 新事项" in out


def test_add_dry_run_changes_nothing(schedule_root: Path, capsys) -> None:
    path = schedule_root / "work/2026/Q4/10/202610-05-11.md"
    before = path.read_bytes()
    code, out, _ = run(
        capsys, "--root", str(schedule_root), "add", "2026-10-05", "预览", "--dry-run"
    )
    assert code == 0
    assert "+-   [ ] `10/05` 预览" in out
    assert path.read_bytes() == before


def test_update_by_match(schedule_root: Path, capsys) -> None:
    code, _, _ = run(
        capsys,
        "--root",
        str(schedule_root),
        "update",
        "--week",
        "2026-10-05",
        "--match",
        "单点时间",
        "--done",
    )
    assert code == 0
    week = parse_file(schedule_root / "work/2026/Q4/10/202610-05-11.md")
    assert week.day(date(2026, 10, 6)).events[0].done is True


def test_delete_by_id(schedule_root: Path, capsys) -> None:
    code, out, _ = run(
        capsys, "--root", str(schedule_root), "delete", "202610-05-11#10/11#1"
    )
    assert code == 0
    assert "deleted" in out
    assert "标签在日期之后" not in (schedule_root / "work/2026/Q4/10/202610-05-11.md").read_text(
        encoding="utf-8"
    )


def test_json_output_for_an_edit(schedule_root: Path, capsys) -> None:
    code, out, _ = run(
        capsys, "--json", "--root", str(schedule_root), "add", "2026-10-05", "JSON 事项"
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["changed"] is True
    assert payload["event"]["id"] == "202610-05-11#10/05#3"


def test_weeks_lists_files(schedule_root: Path, capsys) -> None:
    code, out, _ = run(capsys, "--root", str(schedule_root), "weeks")
    assert code == 0
    assert "202610-05-11.md" in out


def test_exit_code_for_an_ambiguous_match(schedule_root: Path, capsys) -> None:
    code, _, err = run(
        capsys, "--root", str(schedule_root), "update", "--week", "2026-10-05", "--match", "10/0", "--done"
    )
    assert code == 3
    assert "match" in err


def test_exit_code_for_a_missing_event(schedule_root: Path, capsys) -> None:
    code, _, err = run(capsys, "--root", str(schedule_root), "delete", "202610-05-11#10/05#99")
    assert code == 2
    assert "no event" in err


def test_exit_code_for_a_legacy_file(tmp_path: Path, capsys) -> None:
    root = tmp_path / "WLB"
    write_week(root, "# 周安排 01 - 07\n\n## 开发\n\n- [ ] 旧事项\n", monday=date(2019, 4, 1), name="201904-01-07")
    code, _, err = run(capsys, "--root", str(root), "add", "2019-04-01", "x")
    assert code == 4
    assert "read-only" in err


def test_exit_code_for_an_unreadable_date(schedule_root: Path, capsys) -> None:
    code, _, err = run(capsys, "--root", str(schedule_root), "show", "someday")
    assert code == 1
    assert "cannot read" in err


def test_show_of_a_missing_week_explains_itself(schedule_root: Path, capsys) -> None:
    code, _, err = run(capsys, "--root", str(schedule_root), "show", "2027-03-01")
    assert code == 1
    assert "weekly generator" in err


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("today", None),
        ("2026-10-05", "2026-10-05"),
        ("2026/10/05", "2026-10-05"),
        ("20261005", "2026-10-05"),
        ("10/05", f"{date.today().year}-10-05"),
        ("周一", None),
        ("+7d", None),
    ],
)
def test_parse_day_accepts_the_documented_forms(value: str, expected: str | None) -> None:
    result = parse_day(value)
    if expected:
        assert result.isoformat() == expected
    else:
        assert isinstance(result, date)


def test_show_prints_the_note_block(schedule_root: Path, capsys) -> None:
    code, out, _ = run(capsys, "--root", str(schedule_root), "show", "2026-10-05", "--day")
    assert code == 0
    assert "      -   子条目属于上一行" in out
    assert "      -   [ ] 子条目里的复选框不是事件" in out


def test_no_note_keeps_the_list_compact(schedule_root: Path, capsys) -> None:
    code, out, _ = run(
        capsys, "--root", str(schedule_root), "show", "2026-10-05", "--day", "--no-note"
    )
    assert code == 0
    assert "子条目属于上一行" not in out


def test_parse_puts_the_note_in_json(schedule_root: Path, capsys) -> None:
    code, out, _ = run(capsys, "--root", str(schedule_root), "parse", "2026-10-05")
    assert code == 0
    event = json.loads(out)["days"][0]["events"][1]
    assert event["note"].startswith("-   子条目属于上一行")
