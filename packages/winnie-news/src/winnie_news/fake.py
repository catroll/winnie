"""Deterministic offline model for tests and demos.

The AI registry's ``fake`` integration is not a silent fallback for a missing
key; it is an explicit choice. This model answers the pipeline's purposes
with deterministic JSON built from the input, so the whole seven-stage pipeline
can be exercised — and a demo can run — without a vendor.
"""

from __future__ import annotations

import json
import re
from typing import Any

from winnie_core.types import Usage
from winnie_llm import LLMCompletion

from .aggregate.cards import heuristic_card
from .aggregate.models import normalize_event_type
from .categories import derive_category
from .clean.models import CleanItem
from .generate.models import TAG_MIN, Draft

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_WORD_RE = re.compile(r"[\w\u4e00-\u9fff]{2,}")

_FAKE_USAGE = Usage(provider="fake", unit="tokens", quantity=0.0)


class FakeNewsLLM:
    """Answer by purpose; never call a network."""

    name = "fake"

    async def complete(
        self, messages: list[dict[str, Any]], *, purpose: str = ""
    ) -> LLMCompletion:
        system = str(messages[0].get("content") or "")
        user = str(messages[-1].get("content") or "")
        # Write and review are separate purposes, so they are dispatched by
        # purpose: both prompts open with the same role sentence, and matching
        # on prompt text would make a rewrite of that sentence a silent break.
        if purpose == "news.write":
            return _text(self._draft(user))
        if purpose == "news.review":
            return _text(self._review())
        if "fact extractor" in system:
            return _text(self._clean(user))
        if "extract an event card" in system:
            return _text(self._card(user))
        if "deduplication reviewer" in system:
            return _text(self._verify(user))
        return _text({"note": f"fake provider has no answer for purpose {purpose!r}"})

    # ------------------------------------------------------------ purposes

    def _clean(self, user: str) -> dict[str, Any]:
        payload = _payload(user, "material") or {}
        title = str(payload.get("title") or "")
        content = str(payload.get("content") or "")
        return {
            "title": title,
            "summary": title[:60],
            "content_summary": " ".join(content.split())[:300],
            "language": "zh",
            "event_type": _guess_type(title),
            "entities": [],
            "locations": [],
            "event_time": payload.get("published_at"),
            "key_numbers": _numbers(f"{title} {content}")[:6],
            "claims": [title] if title else [],
            "topic_keywords": sorted(_words(title))[:8],
            "quality": {"keep": True, "score": 0.7, "reason": "fake extractor"},
        }

    def _card(self, user: str) -> dict[str, Any]:
        payload = _payload(user, "material") or {}
        try:
            item = CleanItem.model_validate(_to_item(payload))
        except Exception:
            item = CleanItem(
                material_id=str(payload.get("url") or payload.get("title") or "fake"),
                title=str(payload.get("title") or ""),
                content=str(payload.get("content") or ""),
            )
        return heuristic_card(item).model_dump()

    def _verify(self, user: str) -> dict[str, Any]:
        card = _payload(user, "new_card") or {}
        profile = _payload(user, "existing_event") or {}
        card_words = _words(
            " ".join(
                [
                    str(card.get("summary") or ""),
                    " ".join(str(item.get("name") or "") for item in card.get("actors") or []),
                ]
            )
        )
        profile_words = _words(
            " ".join(
                [
                    str(profile.get("summary") or ""),
                    " ".join(str(name) for name in profile.get("actors") or []),
                ]
            )
        )
        shared = card_words & profile_words
        if shared:
            return {
                "decision": "same",
                "confidence": 0.6,
                "reason": f"shared terms: {', '.join(sorted(shared)[:3])}",
                "conflicting_slots": [],
            }
        return {
            "decision": "different",
            "confidence": 0.6,
            "reason": "no shared actor or summary term",
            "conflicting_slots": [],
        }

    def _draft(self, user: str) -> dict[str, Any]:
        payload = _payload(user, "event") or {}
        event = payload.get("event") or {}
        articles = payload.get("articles") or []
        title = str(event.get("title") or "Untitled")
        parts: list[str] = []
        sources: list[dict[str, Any]] = []
        for article in articles:
            excerpt = str(article.get("excerpt") or article.get("summary") or "").strip()
            if excerpt and excerpt not in parts:
                parts.append(excerpt)
            if article.get("url"):
                sources.append(
                    {
                        "title": article.get("title") or "",
                        "url": article.get("url"),
                        "source": article.get("source") or "",
                        "published_at": article.get("published_at"),
                    }
                )
        # 内容规则 1 asks for about 200 characters, so the fake repeats the
        # material to reach it and cuts at a sentence end rather than mid-word.
        source = "\n\n".join(parts) or f"{title}（fake draft）"
        content = source
        while len(content) < 180:
            content += "\n\n" + source
        content = _trim(content, 280)
        return {
            "title": title[:40],
            "category": derive_category(str(event.get("type") or "")),
            "brief": _first_sentence(content),
            "content": content,
            "tags": _tags(event, title),
            "sources": sources,
            "uncertainties": ["offline fake provider produced this draft"],
        }

    @staticmethod
    def _review() -> dict[str, Any]:
        return {
            "passed": True,
            "score": 0.9,
            "problems": [],
            "feedback": "",
        }


def _text(payload: dict[str, Any]) -> LLMCompletion:
    return LLMCompletion(
        text=json.dumps(payload, ensure_ascii=False),
        model="fake",
        usage=_FAKE_USAGE,
    )


def _payload(text: str, tag: str) -> dict[str, Any] | None:
    start = text.find(f"<{tag}>")
    end = text.find(f"</{tag}>")
    if start < 0 or end <= start:
        return None
    raw = text[start + len(tag) + 2 : end].strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _to_item(payload: dict[str, Any]) -> dict[str, Any]:
    """Map the extractor envelope back onto the clean item fields."""
    return {
        "material_id": str(payload.get("url") or payload.get("title") or "fake"),
        "title": str(payload.get("title") or ""),
        "content": str(payload.get("content") or ""),
        "summary": str(payload.get("summary") or ""),
        "source": str(payload.get("source") or ""),
        "url": payload.get("url"),
        "published_at": payload.get("published_at"),
        "language": payload.get("language"),
        "event_type": normalize_event_type(payload.get("event_type_hint") or ""),
        "entities": list(payload.get("entities") or []),
        "locations": list(payload.get("locations") or []),
        "event_time": payload.get("event_time"),
        "key_numbers": list(payload.get("key_numbers") or []),
        "claims": list(payload.get("claims") or []),
        "topic_keywords": list(payload.get("topic_keywords") or []),
        "quality": {"keep": True, "score": 0.7, "reason": "fake extractor"},
    }


def _first_sentence(text: str) -> str:
    """One line for the digest, cut at a boundary rather than mid-word."""
    flat = " ".join(text.split())
    for separator in ("。", "！", "？"):
        index = flat.find(separator)
        if 0 < index < 40:
            return flat[:index]
    if len(flat) <= 40:
        return flat.rstrip("，,、；;")
    head = flat[:40]
    for separator in ("，", "；", "、", ",", ";"):
        index = head.rfind(separator)
        if index >= 12:
            return head[:index]
    return head


def _trim(text: str, limit: int) -> str:
    """Cut at the last sentence end before ``limit``, not mid-sentence."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for separator in ("。", "！", "？"):
        index = head.rfind(separator)
        if index >= 120:
            return head[: index + 1]
    return head.rstrip()


def _tags(event: dict[str, Any], title: str) -> list[str]:
    candidates: list[str] = [str(value) for value in event.get("keywords") or []]
    candidates.extend(
        str(actor.get("name") or "")
        for actor in event.get("actors") or []
        if isinstance(actor, dict)
    )
    candidates.append(title[:12])
    tags: list[str] = []
    for candidate in candidates:
        value = candidate.strip()
        if not value or value in tags:
            continue
        tags.append(value)
        if len(tags) >= 4:
            break
    while len(tags) < TAG_MIN:
        # The fake review passes, but a draft with no tags is a high-severity
        # rule failure; the offline demo must still exercise a clean run.
        tags.append("新闻" if len(tags) == 0 else "事件")
    return tags


def _numbers(text: str) -> list[str]:
    return [match.group(0) for match in _NUMBER_RE.finditer(text)]


def _words(text: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD_RE.finditer(text)}


def _guess_type(title: str) -> str:
    from .aggregate.models import TYPE_HINTS

    for hint, event_type in TYPE_HINTS.items():
        if hint in title:
            return event_type
    return "other"


def build_draft_from_fake(payload: dict[str, Any]) -> Draft:
    """Helper for tests that want the fake draft as a model."""
    return Draft.model_validate(payload)
