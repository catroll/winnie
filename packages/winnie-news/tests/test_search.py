"""Tests for the search stage: queries, result classification and isolation."""

from __future__ import annotations

import pytest

from support import FakeSearchProvider, SearchHit
from winnie_news.aggregate.cards import normalize_card
from winnie_news.aggregate.store import EventStore
from winnie_news.clean.models import CleanItem
from winnie_news.search.queries import build_queries
from winnie_news.search.search import SearchConfig, search_events, usable_hits

CARD = {
    "event_type": "disaster.earthquake",
    "event_time": {"start": "2026-10-21", "granularity": "day"},
    "actors": [{"name": "中国地震台网"}],
    "location": [{"name": "四川雅安", "level": "city"}],
    "action": "测定",
    "object": "6.2级地震",
    "keywords": ["雅安", "地震"],
    "summary": "四川雅安发生6.2级地震",
}


def clean_item(material_id: str = "m1", url: str | None = "https://example.com/a") -> CleanItem:
    return CleanItem(
        material_id=material_id,
        title="四川雅安发生6.2级地震",
        content="body",
        summary="summary",
        source="测试源",
        url=url,
        published_at="2026-10-21T08:00:00+08:00",
    )


def an_event():
    store = EventStore()
    return store.create_event(clean_item(), normalize_card(CARD, clean_item()))


def test_queries_prefer_actor_and_action() -> None:
    queries = build_queries(an_event(), limit=3)
    assert queries[0].startswith("中国地震台网")
    assert any("四川雅安" in query for query in queries)
    assert len(queries) <= 3


@pytest.mark.asyncio
async def test_results_are_classified_and_deduplicated() -> None:
    event = an_event()
    query = build_queries(event, limit=3)[0]
    provider = FakeSearchProvider(
        {
            query: [
                SearchHit("same article", "https://example.com/a?utm=1", "a snippet long enough"),
                SearchHit("new report", "https://news.example.org/b", "a snippet long enough"),
                SearchHit("thin result", "https://news.example.org/c", "short"),
                SearchHit("no url", "", "a snippet long enough"),
            ]
        }
    )
    batch = await search_events([event], provider)
    result = batch.events[0]
    statuses = {hit.url: hit.status for hit in result.hits}
    assert statuses["https://example.com/a?utm=1"] == "known"
    assert statuses["https://news.example.org/b"] == "new"
    assert statuses["https://news.example.org/c"] == "suspect"
    assert "" not in statuses
    assert [hit.url for hit in usable_hits(result)] == ["https://news.example.org/b"]


@pytest.mark.asyncio
async def test_a_failing_query_does_not_discard_the_others() -> None:
    event = an_event()
    query = build_queries(event, limit=3)[0]
    provider = FakeSearchProvider({query: RuntimeError("search backend down")})
    batch = await search_events([event], provider)
    assert batch.events[0].hits == []
    assert batch.events[0].errors
    assert "search" in batch.events[0].errors[0]


@pytest.mark.asyncio
async def test_no_provider_is_a_recorded_skip_not_a_failure() -> None:
    batch = await search_events([an_event()], None)
    assert batch.events[0].hits == []
    assert batch.warnings


@pytest.mark.asyncio
async def test_result_limit_is_respected() -> None:
    event = an_event()
    query = build_queries(event, limit=1)[0]
    provider = FakeSearchProvider(
        {
            query: [
                SearchHit(f"report {index}", f"https://example.org/{index}", "snippet long enough")
                for index in range(5)
            ]
        }
    )
    batch = await search_events([event], provider, config=SearchConfig(results_per_query=2))
    assert len(batch.events[0].hits) == 2
