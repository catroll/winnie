"""Tests for the clean stage: rules, extraction and the second filter."""

from __future__ import annotations

import pytest

from support import ScriptedLLM
from winnie_news.clean.cleaner import CleanConfig, clean_materials
from winnie_news.clean.rules import normalize_title, prepare, rule_filter
from winnie_news.common.material import make_item


def material(title: str, content: str = "body text long enough", **kwargs) -> dict:
    base = {
        "title": title,
        "content": content,
        "source": "Test Source",
        "url": f"https://example.com/{abs(hash(title))}",
        "published_at": "2026-10-21T08:00:00+08:00",
    }
    base.update(kwargs)
    return make_item(**base)


EXTRACTION = {
    "title": "Extracted title",
    "summary": "One neutral sentence.",
    "content_summary": "The body says a thing happened.",
    "language": "zh",
    "event_type": "disaster.earthquake",
    "entities": ["Example Org"],
    "locations": ["Example City"],
    "event_time": "2026-10-21",
    "key_numbers": ["12人"],
    "claims": ["a thing happened"],
    "topic_keywords": ["earthquake", "example city"],
    "quality": {"keep": True, "score": 0.8, "reason": ""},
}


def test_rules_drop_adverts_duplicates_and_empty_titles() -> None:
    items = [
        prepare(material("四川雅安发生6.2级地震", "long enough content here"), max_content_chars=1000),
        prepare(material("点击下载APP，查看更多精彩内容"), max_content_chars=1000),
        prepare(
            material(
                "四川雅安发生6.2级地震",
                "duplicate title long enough",
                url="https://other.example.com/story",
            ),
            max_content_chars=1000,
        ),
        prepare(material("https://example.com/only-a-link"), max_content_chars=1000),
        prepare(
            material("Same link", "different text long enough", url="https://example.com/x"),
            max_content_chars=1000,
        ),
        prepare(
            material("Same link again", "other text long enough", url="https://example.com/x/"),
            max_content_chars=1000,
        ),
    ]
    kept, dropped = rule_filter(items)
    reasons = {entry.reason for entry in dropped}
    assert len(kept) == 2
    assert "navigation_or_advert" in reasons
    assert "duplicate_title" in reasons
    assert "title_is_a_link" in reasons
    assert "duplicate_url" in reasons


def test_title_normalisation_ignores_punctuation_and_case() -> None:
    assert normalize_title("OpenAI 发布 GPT-5！") == normalize_title("openai发布gpt5")


@pytest.mark.asyncio
async def test_clean_merges_extraction_and_keeps_fields() -> None:
    llm = ScriptedLLM({"news.extract": EXTRACTION})
    batch = await clean_materials([material("四川雅安发生6.2级地震")], llm)
    assert batch.count == 1
    item = batch.items[0]
    assert item.title == "Extracted title"
    assert item.summary == "One neutral sentence."
    assert item.locations == ["Example City"]
    assert item.quality.score == 0.8
    assert item.extra["prompt_version"] == "v1"


@pytest.mark.asyncio
async def test_second_filter_drops_rejected_and_low_quality() -> None:
    rejected = {**EXTRACTION, "quality": {"keep": False, "score": 0.9, "reason": "advert"}}
    low = {**EXTRACTION, "quality": {"keep": True, "score": 0.1, "reason": "thin"}}
    llm = ScriptedLLM({"news.extract": [rejected, low]})
    batch = await clean_materials(
        [material("First item title"), material("Second item title")],
        llm,
        config=CleanConfig(min_quality_score=0.3),
    )
    assert batch.count == 0
    reasons = [entry.reason for entry in batch.dropped]
    assert any(reason.startswith("not_news") for reason in reasons)
    assert any(reason.startswith("low_quality") for reason in reasons)


@pytest.mark.asyncio
async def test_one_bad_item_does_not_fail_the_batch() -> None:
    good = EXTRACTION
    llm = ScriptedLLM({"news.extract": [good, RuntimeError("provider exploded")]})
    batch = await clean_materials(
        [material("First item title"), material("Second item title")], llm
    )
    assert batch.count == 1
    assert any(
        entry.stage == "llm" and entry.reason.startswith("extraction_error")
        for entry in batch.dropped
    )


@pytest.mark.asyncio
async def test_total_extraction_failure_raises() -> None:
    llm = ScriptedLLM({"news.extract": RuntimeError("provider down")})
    with pytest.raises(Exception, match="every material item failed"):
        await clean_materials([material("First item title")], llm)
