"""Tests for the review stage: rule checks, verdicts and the retry budget."""

from __future__ import annotations

import pytest

from support import ScriptedLLM
from winnie_news.aggregate.cards import normalize_card
from winnie_news.aggregate.store import EventStore
from winnie_news.clean.models import CleanItem
from winnie_news.generate.models import Draft, DraftSource, WritingRequirements
from winnie_news.review.checks import rule_problems
from winnie_news.review.reviewer import build_messages, review_draft

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
    "当地已启动应急响应，消防救援力量赶赴震中开展排查和救援。\n\n"
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
        "category": "国内要闻",
        "brief": "四川雅安6.2级地震已致12人受伤",
        "content": GOOD_BODY,
        "tags": ["四川", "雅安", "地震"],
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


def problems_of(**overrides):
    return rule_problems(
        a_draft(**overrides),
        an_event(),
        [],
        requirements=WritingRequirements(language="zh"),
    )


PASSED = {"passed": True, "score": 0.9, "problems": [], "feedback": ""}


def test_rules_catch_a_number_that_is_not_in_the_material() -> None:
    problems = problems_of(content=GOOD_BODY.replace("12人", "37人"))
    fact = [problem for problem in problems if problem.type == "fact"]
    assert fact and fact[0].severity == "high"
    assert "37" in fact[0].detail


def test_rules_catch_a_number_invented_in_the_brief() -> None:
    problems = problems_of(brief="四川雅安6.2级地震已致37人受伤")
    fact = [problem for problem in problems if problem.type == "fact"]
    assert fact and "37" in fact[0].detail


def test_a_tag_is_a_label_not_a_fact_claim() -> None:
    problems = problems_of(tags=["2级地震", "地震"])
    assert not any(problem.type == "fact" for problem in problems)


def test_rules_catch_clickbait_and_a_missing_source() -> None:
    problems = problems_of(title="震惊！雅安地震，不看后悔！！", sources=[])
    types = {problem.type for problem in problems}
    assert "style" in types
    assert "format" in types


def test_rules_flag_a_link_in_the_publishable_text() -> None:
    content = GOOD_BODY + "\n\n来源：https://example.com/a1"
    problems = problems_of(content=content)
    assert any(problem.severity == "high" and "link" in problem.detail for problem in problems)


def test_rules_flag_a_named_outlet_citation() -> None:
    problems = problems_of(content=GOOD_BODY.replace("据中国地震台网测定", "据新华社报道"))
    assert any("source attribution" in problem.detail for problem in problems)


def test_rules_allow_the_unverified_markers() -> None:
    content = GOOD_BODY + "\n\n目前伤亡情况据报道仍在核实，尚待核实。"
    problems = problems_of(content=content)
    assert not any("source attribution" in problem.detail for problem in problems)


def test_rules_reject_tag_hygiene_failures() -> None:
    problems = problems_of(tags=["人工智能", "习近平", "中国", "地震", "地震新闻"])
    assert any(problem.type == "compliance" and problem.severity == "high" for problem in problems)
    assert any("人工智能" in problem.detail for problem in problems)
    assert any("标签规则 11" in problem.detail for problem in problems)
    assert any("repeat the same subject" in problem.detail for problem in problems)


def test_rules_reject_a_draft_with_no_tags() -> None:
    problems = problems_of(tags=[])
    assert any(problem.severity == "high" and "no tags" in problem.detail for problem in problems)


def test_rules_flag_a_missing_brief_and_category() -> None:
    problems = problems_of(brief="", category="")
    details = " ".join(problem.detail for problem in problems)
    assert "no brief" in details
    assert "category ''" in details


def test_rules_flag_an_article_delivered_as_one_block() -> None:
    problems = problems_of(content=GOOD_BODY.replace("\n\n", ""))
    assert any("one block" in problem.detail for problem in problems)


def test_rules_flag_markdown_structure() -> None:
    problems = problems_of(content=GOOD_BODY + "\n\n- 震源深度10公里")
    assert any("Markdown structure" in problem.detail for problem in problems)


def test_rules_flag_content_over_the_budget() -> None:
    problems = problems_of(content=GOOD_BODY + "补充说明。" * 40)
    assert any("extendable to 300" in problem.detail for problem in problems)


def test_rules_note_a_draft_under_the_target_length() -> None:
    content = "10月21日，四川雅安发生6.2级地震，已造成12人受伤。\n\n当地已启动应急响应。"
    problems = problems_of(content=content)
    assert any("under the ~200" in problem.detail for problem in problems)


def test_rules_flag_false_balance_when_a_stance_is_set() -> None:
    problems = rule_problems(
        a_draft(content=GOOD_BODY.replace("当地已启动应急响应", "有人认为当地已启动应急响应")),
        an_event(),
        [],
        requirements=WritingRequirements(language="zh", stance="支持开源技术"),
    )
    assert any("hedges" in problem.detail for problem in problems)


def test_the_reviewer_prompt_quotes_both_skills() -> None:
    messages = build_messages(
        a_draft(), an_event(), [], requirements=WritingRequirements(language="zh")
    )
    system = messages[0]["content"]
    assert "# 新闻稿整理" in system
    assert "正文原则上约200字" in system
    assert "# 热点新闻汇总" in system
    assert "默认最近2天、30条" in system


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
        a_draft(content=GOOD_BODY.replace("12人", "37人")), an_event(), [], llm
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
    llm = ScriptedLLM(
        {"news.review": {"passed": False, "score": 0.2, "problems": [], "feedback": "no"}}
    )
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
