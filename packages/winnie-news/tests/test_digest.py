"""Tests for the digest stage: window, order, sections and the three formats."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from winnie_news.categories import CATEGORIES
from winnie_news.common.timeutil import SHANGHAI
from winnie_news.digest.build import build_digest, build_digest_from_store, render
from winnie_news.digest.models import DigestConfig
from winnie_news.generate.models import Draft, DraftSource
from winnie_news.review.models import ReviewResult
from winnie_news.store import NewsStore

NOW = datetime(2026, 11, 1, 9, 30, tzinfo=SHANGHAI)

BODY = (
    "10月31日凌晨3时12分，四川雅安市芦山县发生6.2级地震，震源深度10公里。"
    "截至当天上午，地震已造成12人受伤，部分房屋受损。\n\n"
    "当地已启动应急响应，消防救援力量赶赴震中开展排查和救援，"
    "伤亡数字和财产损失情况仍可能更新。"
)


def a_draft(
    event_id: str = "evt_1",
    *,
    category: str = "国内要闻",
    title: str = "四川雅安发生6.2级地震",
    brief: str = "四川雅安6.2级地震已致12人受伤",
    tags: list[str] | None = None,
    start: str = "2026-10-31",
) -> Draft:
    return Draft(
        event_id=event_id,
        title=title,
        category=category,
        brief=brief,
        content=BODY,
        tags=["四川", "地震"] if tags is None else tags,
        sources=[DraftSource(title="real", url="https://example.com/a1")],
        uncertainties=["伤亡数字仍可能更新"],
        generated_at=f"{start}T10:00:00+08:00",
        revision=1,
    )


def an_event(
    event_type: str = "disaster.earthquake",
    start: str = "2026-10-31",
    locations: list | None = None,
):
    return SimpleNamespace(
        card=SimpleNamespace(
            event_type=event_type,
            event_time=SimpleNamespace(start=start, end=None),
            location=list(locations or []),
        ),
        first_seen=start,
    )


def test_the_window_keeps_only_recent_events() -> None:
    digest = build_digest(
        [(a_draft("evt_1", start="2026-10-31"), "passed"),
         (a_draft("evt_2", start="2026-10-30"), "passed")],
        {},
        now_dt=NOW,
    )
    assert digest.count == 1
    assert digest.excluded["out_of_window"] == 1
    assert (digest.start_date, digest.end_date) == ("2026-10-31", "2026-11-01")


def test_a_draft_that_did_not_pass_review_is_left_out() -> None:
    digest = build_digest([(a_draft(), "needs_human")], {}, now_dt=NOW)
    assert digest.count == 0
    assert digest.excluded["needs_review"] == 1
    assert any("未通过审核" in note for note in digest.notes)
    assert any("实际收录 0 条" in note for note in digest.notes)


def test_including_unreviewed_drafts_is_opt_in() -> None:
    digest = build_digest(
        [(a_draft(), "retry")],
        {},
        config=DigestConfig(include_needs_review=True),
        now_dt=NOW,
    )
    assert digest.count == 1


def test_the_event_date_wins_over_the_write_date() -> None:
    digest = build_digest(
        [(a_draft(), "passed")],
        {"evt_1": an_event(start="2026-10-25")},
        now_dt=NOW,
    )
    assert digest.count == 0
    assert digest.excluded["out_of_window"] == 1


def test_items_are_newest_first_and_grouped_into_four_sections() -> None:
    digest = build_digest(
        [
            (a_draft("evt_1", start="2026-10-30"), "passed"),
            (a_draft("evt_2", category="财经科技", title="某公司发布财报", start="2026-10-31"), "passed"),
        ],
        {},
        config=DigestConfig(window_days=3),
        now_dt=NOW,
    )
    assert [section.category for section in digest.sections] == list(CATEGORIES)
    assert digest.sections[0].items[0].event_id == "evt_1"
    assert digest.sections[2].items[0].event_id == "evt_2"
    # Inside a section the newest item is first.
    ordered = build_digest(
        [
            (a_draft("old", start="2026-10-30"), "passed"),
            (a_draft("new", start="2026-10-31"), "passed"),
        ],
        {},
        config=DigestConfig(window_days=3),
        now_dt=NOW,
    )
    assert [item.event_id for item in ordered.sections[0].items] == ["new", "old"]


def test_the_item_limit_keeps_the_newest() -> None:
    digest = build_digest(
        [
            (a_draft("evt_1", start="2026-10-31"), "passed"),
            (a_draft("evt_2", start="2026-10-31"), "passed"),
            (a_draft("evt_3", start="2026-10-30"), "passed"),
        ],
        {},
        config=DigestConfig(window_days=3, max_items=1),
        now_dt=NOW,
    )
    assert digest.count == 1
    assert digest.items()[0].event_id == "evt_1"
    assert digest.excluded["over_limit"] == 2
    assert any("条数上限" in note for note in digest.notes)


def test_window_days_zero_disables_the_window() -> None:
    digest = build_digest(
        [(a_draft(start="2026-09-01"), "passed")],
        {},
        config=DigestConfig(window_days=0),
        now_dt=NOW,
    )
    assert digest.count == 1
    assert digest.start_date == digest.end_date


def test_a_missing_category_is_derived_from_the_event() -> None:
    event = an_event(
        "politics.diplomacy",
        "2026-10-31",
        [SimpleNamespace(name="美国", level="country")],
    )
    digest = build_digest([(a_draft(category=""), "passed")], {"evt_1": event}, now_dt=NOW)
    assert digest.items()[0].category == "国际要闻"


def test_the_standard_format_has_the_contract_header_and_numbering() -> None:
    digest = build_digest(
        [
            (a_draft("evt_1", start="2026-10-31"), "passed"),
            (a_draft("evt_2", category="体育文娱", title="某队赢得比赛", brief="某队2比1获胜", start="2026-10-31"), "passed"),
        ],
        {},
        now_dt=NOW,
    )
    text = render(digest)
    assert text.startswith("以下为 **2026年10月31日—11月01日** 近2天的 2 条热点新闻")
    assert "按时间线整理" in text
    assert "### 一、国内要闻" in text
    assert "1. **四川雅安发生6.2级地震**：10/31；四川雅安6.2级地震已致12人受伤" in text
    assert "### 四、体育文娱" in text
    assert "（本期无条目）" in text


def test_the_brief_format_uses_the_greeting_and_short_sections() -> None:
    digest = build_digest([(a_draft(), "passed")], {}, now_dt=NOW)
    morning = render(digest, "brief", hour=8)
    assert morning.startswith("📰 早安，以下是今日要闻速览（2026年11月1日）：")
    assert "【国内】" in morning
    assert "1. 四川雅安6.2级地震已致12人受伤" in morning
    assert "【国际】" not in morning
    assert render(digest, "brief", hour=21).startswith("📰 晚安")


def test_the_table_format_escapes_pipes_and_numbers_rows() -> None:
    digest = build_digest(
        [(a_draft(title="标题 | 带竖线", brief="要点"), "passed")],
        {},
        now_dt=NOW,
    )
    text = render(digest, "table")
    assert "| 序号 | 日期 | 分类 | 新闻标题 | 核心要点 |" in text
    assert "| 1 | 10/31 | 国内 | 标题 ／ 带竖线 | 要点 |" in text


def test_an_unknown_format_is_refused() -> None:
    digest = build_digest([(a_draft(), "passed")], {}, now_dt=NOW)
    with pytest.raises(ValueError, match="unknown digest format"):
        render(digest, "podcast")


def test_an_empty_digest_still_renders_the_four_sections() -> None:
    digest = build_digest([], {}, now_dt=NOW)
    text = render(digest)
    assert digest.count == 0
    assert text.count("（本期无条目）") == 4
    assert any("0 条" in note for note in digest.notes)


def test_build_from_store_reads_the_saved_drafts_and_reviews(tmp_path) -> None:
    store = NewsStore(tmp_path)
    run_id = "20261101-093000-abcd"
    store.save_draft(run_id, a_draft())
    store.save_review(
        run_id,
        ReviewResult(
            event_id="evt_1",
            passed=True,
            score=0.9,
            status="passed",
            checked_at="2026-10-31T12:00:00+08:00",
        ),
    )
    digest = build_digest_from_store(store, {}, run_id, now_dt=NOW)
    assert digest.count == 1
    store.save_digest(run_id, digest, fmt="table")
    reloaded = store.load_digest(run_id)
    assert reloaded.count == 1
    assert reloaded.sections[0].items[0].event_id == "evt_1"
