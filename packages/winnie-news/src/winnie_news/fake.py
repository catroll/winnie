"""Deterministic offline model for tests and demos.

The AI registry's ``fake`` integration is not a silent fallback for a missing
key; it is an explicit choice. This model answers the pipeline's four purposes
with deterministic JSON built from the input, so the whole six-stage pipeline can
be exercised — and a demo can run — without a vendor.
"""

from __future__ import annotations

import json
import re
from typing import Any

from winnie_core.types import Usage
from winnie_llm import LLMCompletion

from .aggregate.cards import heuristic_card
from .aggregate.models import normalize_event_type
from .clean.models import CleanItem
from .generate.models import Draft

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
        if "fact extractor" in system:
            return _text(self._clean(user))
        if "extract an event card" in system:
            return _text(self._card(user))
        if "deduplication reviewer" in system:
            return _text(self._verify(user))
        if "careful news writer" in system:
            return _text(self._draft(user))
        if "editor of a news service" in system:
            return _text(self._review())
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
            if excerpt:
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
        body = "\n\n".join(parts) or f"{title}（fake draft）"
        # The review stage rejects a body under 200 characters; repeat the
        # material until the fake draft is long enough to exercise that path.
        source = body
        while len(body) < 260:
            body += "\n\n" + source
        return {
            "title": title[:40],
            "subtitle": None,
            "body": body,
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
