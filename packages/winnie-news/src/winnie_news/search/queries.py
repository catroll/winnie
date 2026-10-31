"""Turn an event card into a small number of search queries.

Queries are built from the card, not from the headline, because a headline is
written to attract a click and often omits the actor or the place. The order is
deliberate: the most specific combination first, then a broader fallback, so a
provider call budget of three still covers the event if the first query is too
narrow.
"""

from __future__ import annotations

from .models import EventSearch
from ..aggregate.models import Event


def build_queries(event: Event, *, limit: int = 3) -> list[str]:
    card = event.card
    actor = card.actors[0].name if card.actors else event.title
    location = card.location[0].name if card.location else ""
    parts: list[str] = []

    primary = " ".join(part for part in (actor, card.action, card.object) if part)
    if primary:
        parts.append(primary)

    with_place = " ".join(
        part for part in (actor, card.action, location) if part
    )
    if with_place and with_place not in parts:
        parts.append(with_place)

    keywords = " ".join(card.keywords[:5])
    if keywords:
        parts.append(f"{actor} {keywords}".strip())

    if not parts:
        parts.append(event.title)

    seen: list[str] = []
    for query in parts:
        cleaned = " ".join(query.split())
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen[:limit]


def empty_event_search(event: Event) -> EventSearch:
    return EventSearch(event_id=event.id)
