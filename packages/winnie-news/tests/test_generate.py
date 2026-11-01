"""Tests for the generate stage: source filtering, contract shape and rendering."""

from __future__ import annotations

import pytest

from support import ScriptedLLM
from winnie_news.aggregate.cards import normalize_card
from winnie_news.aggregate.store import EventStore
from winnie_news.categories import derive_category
from winnie_news.clean.models import CleanItem
from winnie_news.generate.generator import (
    draft_to_markdown,
    draft_to_text,
    generate_draft,
    system_prompt,
)
from winnie_news.generate.models import Draft, WritingRequirements
from winnie_news.ports import LLMError
from winnie_news.search.models import SearchHit

CARD = {
    "event_type": "disaster.earthquake",
    "event_time": {"start": "2026-10-21", "granularity": "day"},
    "actors": [{"name": "中国地震台网"}],
    "location": [{"name": "四川雅安", "level": "city"}],
    "action": "测定",
    "object": "6.2级地震",
    "key_numbers": [{"name": "受伤人数", "value": 12, "unit": "人"}],
    "keywords": ["雅安", "地震"],
    "summary": "四川雅安发生6.2级地震，12人受伤。",
}

ARTICLE = (
    "10月21日凌晨3时12分，四川雅安市芦山县发生6.2级地震，"
    "震源深度10公里，已造成12人受伤，部分房屋受损。"
    "当地已启动应急响应，消防救援力量赶赴震中开展排查和救援。"
)


def an_event(card: dict | None = None):
    item = CleanItem(
        material_id="m1",
        title="四川雅安发生6.2级地震 已致12人受伤",
        content=ARTICLE,
        summary="四川雅安发生6.2级地震，12人受伤。",
        source="中新网",
        url="https://example.com/a1",
        published_at="2026-10-21T08:00:00+08:00",
    )
    store = EventStore()
    return store.create_event(item, normalize_card(card or CARD, item))


DRAFT = {
    "title": "四川雅安发生6.2级地震",
    "category": "国内要闻",
    "brief": "四川雅安6.2级地震已致12人受伤",
    "content": (
        "10月21日凌晨3时12分，四川雅安市芦山县发生6.2级地震，震源深度10公里。"
        "据中国地震台网测定，截至当天上午，地震已造成12人受伤，部分房屋受损。\n\n"
        "当地已启动应急响应，消防救援力量赶赴震中开展排查和救援。"
        "目前暂无人员死亡的报告，伤亡数字和财产损失情况仍可能更新。"
    ),
    "tags": ["四川", "雅安", "地震"],
    "sources": [
        {"title": "四川雅安发生6.2级地震", "url": "https://example.com/a1", "source": "中新网"}
    ],
    "uncertainties": ["伤亡数字仍可能更新"],
}


@pytest.mark.asyncio
async def test_draft_carries_the_contract_fields() -> None:
    llm = ScriptedLLM({"news.write": DRAFT})
    draft = await generate_draft(an_event(), [], llm)
    assert draft.title == "四川雅安发生6.2级地震"
    assert draft.category == "国内要闻"
    assert draft.brief == "四川雅安6.2级地震已致12人受伤"
    assert draft.tags == ["四川", "雅安", "地震"]
    assert [source.url for source in draft.sources] == ["https://example.com/a1"]
    assert draft.uncertainties == ["伤亡数字仍可能更新"]
    assert "10月21日" in draft.content


@pytest.mark.asyncio
async def test_invented_source_url_is_removed_and_reported() -> None:
    invented = {
        **DRAFT,
        "sources": [
            {"title": "real", "url": "https://example.com/a1"},
            {"title": "made up", "url": "https://invented.example.net/x"},
        ],
    }
    llm = ScriptedLLM({"news.write": invented})
    draft = await generate_draft(an_event(), [], llm)
    assert [source.url for source in draft.sources] == ["https://example.com/a1"]
    assert any("invented" in note for note in draft.uncertainties)


@pytest.mark.asyncio
async def test_no_attribution_falls_back_to_the_event_articles() -> None:
    llm = ScriptedLLM({"news.write": {**DRAFT, "sources": []}})
    draft = await generate_draft(an_event(), [], llm)
    assert [source.url for source in draft.sources] == ["https://example.com/a1"]
    assert any("did not attribute" in note for note in draft.uncertainties)


@pytest.mark.asyncio
async def test_search_hit_urls_are_citable() -> None:
    hit = SearchHit(
        title="follow-up",
        url="https://news.example.org/b",
        snippet="snippet",
        source="example.org",
    )
    draft_payload = {**DRAFT, "sources": [{"title": "x", "url": hit.url}]}
    llm = ScriptedLLM({"news.write": draft_payload})
    draft = await generate_draft(an_event(), [hit], llm)
    assert [source.url for source in draft.sources] == [hit.url]


@pytest.mark.asyncio
async def test_short_content_is_rejected() -> None:
    llm = ScriptedLLM({"news.write": {**DRAFT, "content": "too short"}})
    with pytest.raises(LLMError, match="too short"):
        await generate_draft(an_event(), [], llm)


@pytest.mark.asyncio
async def test_tags_are_normalised_and_capped() -> None:
    payload = {
        **DRAFT,
        "tags": [
            "#AI",
            "AI",
            " ai ",
            "大模型",
            "上海",
            "楼市",
            "经济",
            "新闻",
            "第九个",
        ],
    }
    llm = ScriptedLLM({"news.write": payload})
    draft = await generate_draft(an_event(), [], llm)
    assert draft.tags == ["AI", "大模型", "上海", "楼市", "经济", "新闻", "第九个"]


@pytest.mark.asyncio
async def test_a_comma_separated_tag_string_is_accepted() -> None:
    llm = ScriptedLLM({"news.write": {**DRAFT, "tags": "四川，地震、救援"}})
    draft = await generate_draft(an_event(), [], llm)
    assert draft.tags == ["四川", "地震", "救援"]


@pytest.mark.asyncio
async def test_a_missing_brief_falls_back_to_the_headline() -> None:
    llm = ScriptedLLM({"news.write": {**DRAFT, "brief": ""}})
    draft = await generate_draft(an_event(), [], llm)
    assert draft.brief == "四川雅安发生6.2级地震"


@pytest.mark.asyncio
async def test_a_category_alias_is_normalised() -> None:
    llm = ScriptedLLM({"news.write": {**DRAFT, "category": "财经"}})
    draft = await generate_draft(an_event(), [], llm)
    assert draft.category == "财经科技"


@pytest.mark.asyncio
async def test_an_unknown_category_falls_back_to_the_event_type() -> None:
    card = {**CARD, "event_type": "corporate.earnings"}
    llm = ScriptedLLM({"news.write": {**DRAFT, "category": "misc"}})
    draft = await generate_draft(an_event(card), [], llm)
    assert draft.category == "财经科技"


@pytest.mark.asyncio
async def test_a_foreign_country_makes_the_fallback_international() -> None:
    card = {
        **CARD,
        "event_type": "politics.diplomacy",
        "location": [{"name": "美国", "level": "country"}],
    }
    llm = ScriptedLLM({"news.write": {**DRAFT, "category": ""}})
    draft = await generate_draft(an_event(card), [], llm)
    assert draft.category == "国际要闻"


def test_category_fallback_by_event_type() -> None:
    assert derive_category("sports.match") == "体育文娱"
    assert derive_category("tech.product") == "财经科技"
    assert derive_category("disaster.flood") == "国内要闻"
    assert derive_category("politics.diplomacy") == "国内要闻"


@pytest.mark.asyncio
async def test_the_article_may_be_returned_as_body() -> None:
    """A model that still answers with the old field name is understood."""
    payload = {key: value for key, value in DRAFT.items() if key != "content"}
    payload["body"] = DRAFT["content"]
    llm = ScriptedLLM({"news.write": payload})
    draft = await generate_draft(an_event(), [], llm)
    assert draft.content == DRAFT["content"]


def test_a_legacy_draft_stored_as_body_still_loads() -> None:
    draft = Draft.model_validate(
        {
            "event_id": "evt_1",
            "title": "旧稿件",
            "body": "这是旧版本的正文。",
            "generated_at": "2026-10-21T10:00:00+08:00",
        }
    )
    assert draft.content == "这是旧版本的正文。"


@pytest.mark.asyncio
async def test_markdown_is_the_published_text_without_a_source_list() -> None:
    llm = ScriptedLLM({"news.write": DRAFT})
    draft = await generate_draft(an_event(), [], llm)
    markdown = draft_to_markdown(draft)
    assert markdown.startswith("# 四川雅安发生6.2级地震")
    assert "tags: 四川, 雅安, 地震" in markdown
    assert "example.com" not in markdown
    assert "## Sources" not in markdown
    assert "## Uncertainties" not in markdown


@pytest.mark.asyncio
async def test_the_plain_text_form_is_title_content_tags() -> None:
    llm = ScriptedLLM({"news.write": DRAFT})
    draft = await generate_draft(an_event(), [], llm)
    text = draft_to_text(draft)
    lines = text.splitlines()
    assert lines[0] == "title"
    assert lines[1] == "四川雅安发生6.2级地震"
    assert lines[2] == "content"
    assert "10月21日" in text
    assert "tags" in lines
    assert lines[lines.index("tags") + 1] == "四川, 雅安, 地震"
    assert "example.com" not in text
    # The contract labels the fields and nothing else.
    labels = [line for line in lines if line in {"title", "content", "tags"}]
    assert labels == ["title", "content", "tags"]


def test_the_writer_prompt_quotes_both_skills() -> None:
    text = system_prompt(WritingRequirements(language="zh"))
    assert "# 新闻稿整理" in text
    assert "正文原则上约200字" in text
    assert "# 热点新闻汇总" in text
    assert "默认最近2天、30条" in text


def test_writing_requirements_reject_inverted_limits() -> None:
    with pytest.raises(ValueError, match="writing limits"):
        WritingRequirements(target_chars=400, max_chars=300)
