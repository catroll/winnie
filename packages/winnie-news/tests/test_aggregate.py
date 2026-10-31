"""Tests for the aggregate stage: keys, scoring, decisions and the store."""

from __future__ import annotations

import pytest

from support import ScriptedLLM
from winnie_news.aggregate.aggregator import aggregate_items
from winnie_news.aggregate.cards import normalize_card
from winnie_news.aggregate.keys import card_keys, norm_key
from winnie_news.aggregate.models import EventCard, normalize_event_type

from winnie_news.aggregate.store import AggregateConfig, EventStore
from winnie_news.clean.models import CleanItem

CARD = {
    "event_type": "disaster.earthquake",
    "event_time": {"start": "2026-10-21", "granularity": "day"},
    "actors": [{"name": "中国地震台网"}],
    "location": [{"name": "四川雅安", "level": "city"}],
    "action": "测定",
    "object": "6.2级地震",
    "key_numbers": [{"name": "震级", "value": 6.2, "unit": "级"}],
    "keywords": ["雅安", "地震", "6.2级"],
    "summary": "四川雅安发生6.2级地震",
    "confidence": 0.8,
}




def _extract_then_verify(card_for, *verdicts):
    """One handler for both calls that share the news.extract purpose.

    Extraction replies are built from the item in the prompt; verification
    replies are consumed in order, the last one repeating.
    """
    queue = list(verdicts)

    def handler(messages):
        system = str(messages[0].get("content") or "")
        user = str(messages[-1].get("content") or "")
        if "deduplication reviewer" in system:
            if len(queue) > 1:
                return queue.pop(0)
            return queue[0] if queue else {
                "decision": "different",
                "confidence": 0.5,
                "reason": "no scripted verdict",
            }
        return card_for(user)

    return handler

def item(
    material_id: str,
    title: str,
    *,
    content: str = "",
    url: str | None = None,
    source: str = "测试源",
) -> CleanItem:
    return CleanItem(
        material_id=material_id,
        title=title,
        content=content or title,
        summary=title,
        source=source,
        url=url,
        published_at="2026-10-21T08:00:00+08:00",
    )


def card(**overrides) -> dict:
    payload = {**CARD, **overrides}
    return payload


def test_event_type_normalisation() -> None:
    assert normalize_event_type("地震") == "disaster.earthquake"
    assert normalize_event_type("earthquake") == "disaster.earthquake"
    assert normalize_event_type("disaster.earthquake") == "disaster.earthquake"
    assert normalize_event_type("something invented") == "other"


def test_card_keys_normalise_actors_and_numbers() -> None:
    event_card = EventCard.model_validate(CARD)
    keys = card_keys(event_card)
    assert norm_key("中国地震台网") in keys.actors
    assert keys.object_key == norm_key("6.2级地震")
    assert any(spec.startswith(norm_key("震级")) for spec in keys.numbers)


def test_hard_conflicts_reject_a_candidate() -> None:
    store = EventStore()
    first = store.create_event(item("m1", "四川雅安发生6.2级地震"), normalize_card(CARD, item("m1", "x")))
    other_place = card(
        location=[{"name": "北京", "level": "city"}],
        actors=[{"name": "北京市地震局"}],
    )
    candidate = store.score_candidate(
        normalize_card(other_place, item("m2", "x")), first, "2026-10-21", AggregateConfig()
    )
    assert candidate is None

    different_type = card(event_type="corporate.earnings")
    assert (
        store.score_candidate(
            normalize_card(different_type, item("m3", "x")),
            first,
            "2026-10-21",
            AggregateConfig(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_same_event_merges_and_keeps_both_articles() -> None:
    store = EventStore()
    llm = ScriptedLLM({"news.extract": _extract_then_verify(lambda user: CARD)})
    batch = await aggregate_items(
        [
            item("m1", "四川雅安发生6.2级地震", url="https://example.com/1"),
            item("m2", "雅安芦山6.2级地震最新消息", url="https://example.com/2"),
        ],
        store,
        llm,
    )
    assert len(store.events) == 1
    event = next(iter(store.events.values()))
    assert len(event.articles) == 2
    assert event.coverage.sources == 1  # both fixture articles share one source
    assert {decision.decision for decision in batch.decisions} == {"new", "merge"}
    merge = next(decision for decision in batch.decisions if decision.decision == "merge")
    assert merge.method == "auto"


@pytest.mark.asyncio
async def test_gray_zone_asks_the_model_and_honours_its_verdict() -> None:
    store = EventStore()
    similar = card(
        location=[],
        object="另一次地震",
        key_numbers=[],
        keywords=["雅安", "地震"],
    )
    verify_same = {"decision": "same", "confidence": 0.7, "reason": "same quake"}
    verify_different = {
        "decision": "different",
        "confidence": 0.7,
        "reason": "another quake",
        "conflicting_slots": ["object"],
    }
    llm = ScriptedLLM(
        {
            "news.extract": _extract_then_verify(
                lambda user: CARD if "6.2级" in user else similar,
                verify_same,
                verify_different,
            )
        }
    )
    batch = await aggregate_items(
        [
            item("m1", "四川雅安发生6.2级地震", url="https://example.com/1"),
            item("m2", "雅安地震", url="https://example.com/2"),
            item("m3", "雅安再次地震", url="https://example.com/3"),
        ],
        store,
        llm,
    )
    methods = [decision.method for decision in batch.decisions if decision.decision == "merge"]
    assert "llm" in methods
    assert any(decision.decision == "new" and decision.method == "llm" for decision in batch.decisions)


@pytest.mark.asyncio
async def test_uncertain_verdict_marks_the_event_for_review() -> None:
    store = EventStore()
    similar = card(location=[], object="另一次地震", key_numbers=[], keywords=["雅安", "地震"])
    uncertain = {"decision": "uncertain", "confidence": 0.2, "reason": "cannot tell"}
    llm = ScriptedLLM(
        {
            "news.extract": _extract_then_verify(
                lambda user: CARD if "6.2级" in user else similar,
                uncertain,
            )
        }
    )
    batch = await aggregate_items(
        [
            item("m1", "四川雅安发生6.2级地震"),
            item("m2", "雅安地震"),
        ],
        store,
        llm,
    )
    assert any(decision.decision == "review" for decision in batch.decisions)
    assert any(event.status == "needs_review" for event in store.events.values())


@pytest.mark.asyncio
async def test_an_article_is_never_attached_twice() -> None:
    store = EventStore()
    llm = ScriptedLLM({"news.extract": _extract_then_verify(lambda user: CARD)})
    first = await aggregate_items([item("m1", "四川雅安发生6.2级地震")], store, llm)
    assert first.decisions[0].decision == "new"

    second = await aggregate_items([item("m1", "四川雅安发生6.2级地震")], store, llm)
    assert second.decisions[0].decision == "skipped"
    assert len(next(iter(store.events.values())).articles) == 1


def test_store_round_trips_through_a_file(tmp_path) -> None:
    store = EventStore()
    store.create_event(item("m1", "四川雅安发生6.2级地震"), normalize_card(CARD, item("m1", "x")))
    path = tmp_path / "events.json"
    store.save(path)

    loaded = EventStore()
    assert loaded.load(path) == 1
    assert len(loaded.events) == 1
    assert norm_key("中国地震台网") in {norm_key(actor) for actor in loaded.events.popitem()[1].actors}
