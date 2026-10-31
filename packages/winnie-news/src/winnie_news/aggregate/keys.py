"""Normalised keys and similarity for comparing event cards.

Blocking and scoring both operate on normalised phrases: NFKC, lower case,
punctuation as a separator, and for set keys no whitespace at all. This makes
"OpenAI" and "openai" the same actor without making "New York" and "NewYork"
different places.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from winnie_news.common.timeutil import SHANGHAI, normalize_time

from .models import EventCard

PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return " ".join(PUNCT_RE.sub(" ", text).split())


def norm_key(value: Any) -> str:
    return norm_text(value).replace(" ", "")


def phrase_set(values: Any) -> set[str]:
    if not values:
        return set()
    if isinstance(values, str):
        values = [values]
    return {key for key in (norm_key(item) for item in values) if key}


def parse_dt(value: Any) -> datetime | None:
    text = normalize_time(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def days_between(left: datetime, right: datetime) -> float:
    return abs((left - right).total_seconds()) / 86400


def weighted_jaccard(
    left: set[str], right: set[str], idf: Callable[[str], float]
) -> float:
    """Jaccard weighted by inverse document frequency.

    A shared rare actor is evidence; a shared common word is not. The weight
    comes from how many events in the store contain the token.
    """
    if not left or not right:
        return 0.0
    intersection = left & right
    if not intersection:
        return 0.0
    union = left | right
    denominator = sum(idf(token) for token in union)
    if denominator <= 0:
        return 0.0
    return sum(idf(token) for token in intersection) / denominator


@dataclass(frozen=True)
class CardKeys:
    actors: set[str]
    keywords: set[str]
    locations: list[dict[str, Any]]
    numbers: set[str]
    object_key: str
    action_key: str


def card_keys(card: EventCard) -> CardKeys:
    actors: set[str] = set()
    for entity in card.actors:
        actors |= phrase_set(entity.name)
        actors |= phrase_set(entity.aliases)

    locations = [
        entity.model_dump()
        for entity in card.location
        if norm_key(entity.name)
    ]
    numbers = {
        f"{norm_key(fact.name)}={norm_text(fact.value)}"
        for fact in card.key_numbers
        if norm_key(fact.name)
    }
    return CardKeys(
        actors=actors,
        keywords=phrase_set(card.keywords),
        locations=locations,
        numbers=numbers,
        object_key=norm_key(card.object),
        action_key=norm_key(card.action),
    )


def idf_from_total(total: int, document_frequency: int) -> float:
    return math.log((max(total, 1) + 1) / (document_frequency + 1)) + 1.0
