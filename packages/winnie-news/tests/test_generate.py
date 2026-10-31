"""Tests for the generate stage: source filtering, validation and rendering."""

from __future__ import annotations

import pytest

from support import ScriptedLLM
from winnie_news.aggregate.cards import normalize_card
from winnie_news.aggregate.store import EventStore
from winnie_news.clean.models import CleanItem
from winnie_news.generate.generator import (
    draft_to_markdown,
    generate_draft,
)
from winnie_news.generate.models import WritingRequirements
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


def an_event():
    item = CleanItem(
        material_id="m1",
        title="四川雅安发生6.2级地震 已致12人受伤",
        content=(
            "10月21日凌晨3时12分，四川雅安市芦山县发生6.2级地震，"
            "震源深度10公里，已造成12人受伤，部分房屋受损。"
        ),
        summary="四川雅安发生6.2级地震，12人受伤。",
        source="中新网",
        url="https://example.com/a1",
        published_at="2026-10-21T08:00:00+08:00",
    )
    store = EventStore()
    return store.create_event(item, normalize_card(CARD, item))


DRAFT = {
    "title": "四川雅安发生6.2级地震",
    "subtitle": None,
    "body": (
        "10月21日凌晨3时12分，四川雅安市芦山县发生6.2级地震，震源深度10公里。"
        "据中国地震台网测定，截至当天上午，地震已造成12人受伤，部分房屋受损。"
        "当地已启动应急响应，消防救援力量赶赴震中开展排查和救援。"
        "目前暂无人员死亡的报告，伤亡数字和财产损失情况仍可能更新，后续信息有待官方进一步通报。"
    ),
    "sources": [{"title": "四川雅安发生6.2级地震", "url": "https://example.com/a1", "source": "中新网"}],
    "uncertainties": ["伤亡数字仍可能更新"],
}


@pytest.mark.asyncio
async def test_draft_uses_supplied_sources() -> None:
    llm = ScriptedLLM({"news.write": DRAFT})
    draft = await generate_draft(an_event(), [], llm)
    assert draft.title == "四川雅安发生6.2级地震"
    assert [source.url for source in draft.sources] == ["https://example.com/a1"]
    assert draft.uncertainties == ["伤亡数字仍可能更新"]
    assert "10月21日" in draft.body


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
    hit = SearchHit(title="follow-up", url="https://news.example.org/b", snippet="snippet", source="example.org")
    draft_payload = {**DRAFT, "sources": [{"title": "x", "url": hit.url}]}
    llm = ScriptedLLM({"news.write": draft_payload})
    draft = await generate_draft(an_event(), [hit], llm)
    assert [source.url for source in draft.sources] == [hit.url]


@pytest.mark.asyncio
async def test_short_body_is_rejected() -> None:
    llm = ScriptedLLM({"news.write": {**DRAFT, "body": "too short"}})
    with pytest.raises(LLMError, match="too short"):
        await generate_draft(an_event(), [], llm)


@pytest.mark.asyncio
async def test_markdown_contains_title_body_sources_and_uncertainties() -> None:
    llm = ScriptedLLM({"news.write": DRAFT})
    draft = await generate_draft(
        an_event(), [], llm, requirements=WritingRequirements(language="zh")
    )
    markdown = draft_to_markdown(draft)
    assert markdown.startswith("# 四川雅安发生6.2级地震")
    assert "## Sources" in markdown
    assert "(https://example.com/a1)" in markdown
    assert "## Uncertainties" in markdown
