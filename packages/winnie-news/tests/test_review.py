"""Tests for the review stage: rule checks, verdicts and the retry budget."""

from __future__ import annotations

import pytest

from support import ScriptedLLM
from winnie_news.aggregate.cards import normalize_card
from winnie_news.aggregate.store import EventStore
from winnie_news.clean.models import CleanItem
from winnie_news.generate.models import Draft, DraftSource, WritingRequirements
from winnie_news.review.checks import rule_problems
from winnie_news.review.reviewer import review_draft

CARD = {
    "event_type": "disaster.earthquake",
    "event_time": {"start": "2026-10-21", "granularity": "day"},
    "actors": [{"name": "中国地震台网"}],
    "location": [{"name": "四川雅安", "level": "city"}],
    "key_numbers": [{"name": "受伤人数", "value": 12, "unit": "人"}],
    "keywords": ["雅安", "地震"],
    "summary": "12人受伤",
}

GOOD_BODY = (
    "10月21日凌晨3时12分，四川雅安市芦山县发生6.2级地震，震源深度10公里。"
    "据中国地震台网测定，截至当天上午，地震已造成12人受伤，部分房屋受损。"
    "当地已启动应急响应，消防救援力量赶赴震中开展排查和救援。"
    "地震发生后，当地多个乡镇出现停电，通信一度中断。"
    "当地学校和企业已按预案组织人员疏散，震区电力、通信正在逐步恢复。"
    "目前暂无人员死亡的报告，伤亡数字和财产损失情况仍可能更新，后续信息有待官方进一步通报。"
)


def an_event():
    item = CleanItem(
        material_id="m1",
        title="四川雅安发生6.2级地震 已致12人受伤",
        content=(
            "10月21日凌晨3时12分，四川雅安市芦山县发生6.2级地震，"
            "震源深度10公里，已造成12人受伤，部分房屋受损。"
        ),
        summary="12人受伤",
        source="中新网",
        url="https://example.com/a1",
        published_at="2026-10-21T08:00:00+08:00",
    )
    store = EventStore()
    return store.create_event(item, normalize_card(CARD, item))


def a_draft(**overrides) -> Draft:
    payload = {
        "event_id": "evt_1",
        "title": "四川雅安发生6.2级地震",
        "body": GOOD_BODY,
        "sources": [
            DraftSource(
                title="四川雅安发生6.2级地震",
                url="https://example.com/a1",
                source="中新网",
            )
        ],
        "uncertainties": ["伤亡数字仍可能更新"],
        "language": "zh",
        "model": "scripted",
        "generated_at": "2026-10-21T10:00:00+08:00",
    }
    payload.update(overrides)
    return Draft.model_validate(payload)


PASSED = {"passed": True, "score": 0.9, "problems": [], "feedback": ""}


def test_rules_catch_a_number_that_is_not_in_the_material() -> None:
    event = an_event()
    draft = a_draft(body=GOOD_BODY.replace("12人", "37人"))
    problems = rule_problems(
        draft, event, [], requirements=WritingRequirements(language="zh")
    )
    fact = [problem for problem in problems if problem.type == "fact"]
    assert fact and fact[0].severity == "high"
    assert "37" in fact[0].detail


def test_rules_catch_clickbait_and_a_missing_source() -> None:
    event = an_event()
    draft = a_draft(title="震惊！雅安地震，不看后悔！！", sources=[])
    problems = rule_problems(
        draft, event, [], requirements=WritingRequirements(language="zh")
    )
    types = {problem.type for problem in problems}
    assert "style" in types
    assert "format" in types


@pytest.mark.asyncio
async def test_a_clean_draft_passes() -> None:
    llm = ScriptedLLM({"news.review": PASSED})
    result = await review_draft(a_draft(), an_event(), [], llm)
    assert result.passed
    assert result.status == "passed"
    assert result.problems == []


@pytest.mark.asyncio
async def test_a_high_rule_problem_blocks_a_passing_model_review() -> None:
    llm = ScriptedLLM({"news.review": PASSED})
    result = await review_draft(
        a_draft(body=GOOD_BODY.replace("12人", "37人")), an_event(), [], llm
    )
    assert not result.passed
    assert result.status == "retry"
    assert "37" in result.feedback


@pytest.mark.asyncio
async def test_model_problems_are_merged_and_retry_is_allowed() -> None:
    reply = {
        "passed": False,
        "score": 0.4,
        "problems": [
            {"type": "completeness", "severity": "high", "detail": "missing official response"}
        ],
        "feedback": "Add the local government's statement.",
    }
    llm = ScriptedLLM({"news.review": reply})
    result = await review_draft(a_draft(), an_event(), [], llm, retry=0, max_retries=2)
    assert result.status == "retry"
    assert "Add the local government" in result.feedback
    assert any(problem.origin == "llm" for problem in result.problems)


@pytest.mark.asyncio
async def test_retry_budget_exhaustion_marks_needs_human() -> None:
    llm = ScriptedLLM({"news.review": {"passed": False, "score": 0.2, "problems": [], "feedback": "no"}})
    result = await review_draft(a_draft(), an_event(), [], llm, retry=2, max_retries=2)
    assert result.status == "needs_human"
    assert not result.passed


@pytest.mark.asyncio
async def test_a_reviewer_failure_never_passes_a_draft() -> None:
    llm = ScriptedLLM({"news.review": RuntimeError("provider down")})
    result = await review_draft(a_draft(), an_event(), [], llm)
    assert not result.passed
    assert result.status == "retry"
    assert any("reviewer failed" in problem.detail for problem in result.problems)
