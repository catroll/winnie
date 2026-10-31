"""In-memory event store: recall, scoring and persistence.

The store is the aggregation stage's only state. It is deliberately small enough
to rebuild from a JSON file: a personal deployment holds thousands of events,
not millions, and a file the user can read and diff beats a schema they cannot
inspect. The inverted indexes are derived and rebuilt on load.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from winnie_news.clean.models import CleanItem
from winnie_news.common.timeutil import now, now_iso

from .keys import (
    CardKeys,
    card_keys,
    days_between,
    idf_from_total,
    norm_key,
    parse_dt,
    weighted_jaccard,
)
from .models import (
    ArticleRef,
    Event,
    EventBatch,
    EventCard,
    NamedEntity,
    type_family,
    TYPE_WINDOW_DAYS,
)

logger = logging.getLogger(__name__)


class AggregateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_threshold: float = 0.85
    gray_threshold: float = 0.55
    window_days: int = 7
    max_candidates: int = 50
    max_articles_per_event: int = 200
    # A candidate below this is not worth an LLM verification call.
    verify_concurrency: int = 3


@dataclass(frozen=True)
class Candidate:
    event_id: str
    score: float
    anchor: bool
    details: dict[str, float]


class EventStore:
    def __init__(self) -> None:
        self.events: dict[str, Event] = {}
        self.article_ids: set[str] = set()
        self.by_type: defaultdict[str, set[str]] = defaultdict(set)
        self.by_family: defaultdict[str, set[str]] = defaultdict(set)
        self.by_actor: defaultdict[str, set[str]] = defaultdict(set)
        self.by_keyword: defaultdict[str, set[str]] = defaultdict(set)
        self.df_actor: Counter[str] = Counter()
        self.df_keyword: Counter[str] = Counter()

    # ------------------------------------------------------------ indexes

    def _index(self, event: Event, keys: CardKeys) -> None:
        for actor in keys.actors:
            if actor and actor not in {norm_key(a) for a in event.actors}:
                event.actors = sorted(set(event.actors) | {actor})
                self.by_actor[actor].add(event.id)
                self.df_actor[actor] += 1
        for keyword in keys.keywords:
            if keyword and keyword not in set(event.keywords):
                event.keywords = sorted(set(event.keywords) | {keyword})
                self.by_keyword[keyword].add(event.id)
                self.df_keyword[keyword] += 1
        for location in keys.locations:
            key = norm_key(location.get("name"))
            if key and all(norm_key(item.name) != key for item in event.locations):
                event.locations.append(NamedEntity.model_validate(location))

    def add_event(self, event: Event) -> None:
        self.events[event.id] = event
        event_type = str(event.card.event_type or "other")
        self.by_type[event_type].add(event.id)
        self.by_family[type_family(event_type)].add(event.id)
        for article in event.articles:
            self._index(event, card_keys(article.card))
            if article.material_id:
                self.article_ids.add(article.material_id)
        event.refresh()

    def _idf_actor(self, token: str) -> float:
        return idf_from_total(len(self.events), self.df_actor.get(token, 0))

    def _idf_keyword(self, token: str) -> float:
        return idf_from_total(len(self.events), self.df_keyword.get(token, 0))

    # ------------------------------------------------------------ recall

    def recall(
        self, card: EventCard, published_at: Any, config: AggregateConfig
    ) -> list[str]:
        keys = card_keys(card)
        scores: Counter[str] = Counter()
        for actor in keys.actors:
            for event_id in self.by_actor.get(actor, ()):
                scores[event_id] += 3
        for keyword in keys.keywords:
            for event_id in self.by_keyword.get(keyword, ()):
                scores[event_id] += 1
        for event_id in self.by_type.get(card.event_type, ()):
            scores[event_id] += 1
        for event_id in self.by_family.get(type_family(card.event_type), ()):
            scores[event_id] += 1

        article_time = parse_dt(published_at)
        window = self.window_days(card.event_type, config)
        ranked: list[tuple[int, str]] = []
        for event_id, score in scores.most_common(config.max_candidates * 3):
            event = self.events.get(event_id)
            if event is None:
                continue
            if len(event.articles) >= config.max_articles_per_event:
                continue
            event_time = parse_dt(event.card.event_time.start) or parse_dt(
                event.first_seen
            )
            if (
                article_time
                and event_time
                and days_between(article_time, event_time) > window
            ):
                continue
            ranked.append((score, event_id))
        return [event_id for _, event_id in ranked[: config.max_candidates]]

    @staticmethod
    def window_days(event_type: str, config: AggregateConfig) -> int:
        return TYPE_WINDOW_DAYS.get(type_family(event_type), config.window_days)

    # ------------------------------------------------------------ scoring

    def score_candidate(
        self,
        card: EventCard,
        event: Event,
        published_at: Any,
        config: AggregateConfig,
    ) -> Candidate | None:
        card_type = str(card.event_type or "other")
        event_type = str(event.card.event_type or "other")
        if self._type_conflict(card_type, event_type):
            return None

        keys = card_keys(card)
        event_locations = [item.model_dump() for item in event.locations]
        if self._locations_conflict(keys.locations, event_locations):
            return None

        article_time = parse_dt(card.event_time.start) or parse_dt(published_at)
        event_time = parse_dt(event.card.event_time.start) or parse_dt(event.first_seen)
        window = self.window_days(card_type, config)
        if (
            article_time
            and event_time
            and days_between(article_time, event_time) > window
        ):
            return None

        event_actor_keys = {norm_key(actor) for actor in event.actors}
        event_keyword_keys = {norm_key(keyword) for keyword in event.keywords}
        actor_sim = self._weighted_jaccard(
            keys.actors, event_actor_keys, self._idf_actor
        )
        keyword_sim = self._weighted_jaccard(
            keys.keywords, event_keyword_keys, self._idf_keyword
        )
        object_sim = self._object_sim(keys, event)
        number_sim, number_hit = self._number_sim(
            keys.numbers, self._event_numbers(event)
        )
        time_sim = self._time_sim(article_time, event_time, window)
        type_sim = self._type_sim(card_type, event_type)

        score = (
            0.30 * actor_sim
            + 0.20 * object_sim
            + 0.20 * keyword_sim
            + 0.15 * number_sim
            + 0.10 * time_sim
            + 0.05 * type_sim
        )
        shared_actors = keys.actors & event_actor_keys
        rare_actor = any(self._idf_actor(actor) >= 1.5 for actor in shared_actors)
        object_hit = bool(keys.object_key and keys.object_key in self._event_object_keys(event))
        anchor = rare_actor or number_hit or object_hit
        return Candidate(
            event_id=event.id,
            score=round(score, 4),
            anchor=anchor,
            details={
                "actor": round(actor_sim, 4),
                "object": round(object_sim, 4),
                "keyword": round(keyword_sim, 4),
                "number": round(number_sim, 4),
                "time": round(time_sim, 4),
                "type": round(type_sim, 4),
            },
        )

    @staticmethod
    def _weighted_jaccard(left, right, idf) -> float:
        return weighted_jaccard(left, right, idf)

    @staticmethod
    def _type_conflict(left: str, right: str) -> bool:
        if left == right or "other" in (left, right):
            return False
        return type_family(left) != type_family(right)

    @staticmethod
    def _type_sim(left: str, right: str) -> float:
        if left == right:
            return 1.0
        if "other" in (left, right):
            return 0.3
        return 0.5 if type_family(left) == type_family(right) else 0.0

    @staticmethod
    def _locations_conflict(
        left: list[dict[str, Any]], right: list[dict[str, Any]]
    ) -> bool:
        if not left or not right:
            return False
        for a in left:
            for b in right:
                left_name = norm_key(a.get("name"))
                right_name = norm_key(b.get("name"))
                if left_name and right_name and (
                    left_name in right_name or right_name in left_name
                ):
                    return False
        broad = {"country", "region", "province", "state"}
        left_broad = [
            norm_key(item.get("name"))
            for item in left
            if str(item.get("level") or "") in broad
        ]
        right_broad = [
            norm_key(item.get("name"))
            for item in right
            if str(item.get("level") or "") in broad
        ]
        # Two different countries conflict; country vs city is a hierarchy.
        if left_broad and right_broad:
            return True
        if left_broad or right_broad:
            return False
        return True

    @staticmethod
    def _event_object_keys(event: Event) -> set[str]:
        return {norm_key(event.card.object), norm_key(event.card.action)} - {""}

    def _object_sim(self, keys: CardKeys, event: Event) -> float:
        if keys.object_key and keys.object_key in self._event_object_keys(event):
            return 1.0
        left = {keys.object_key, keys.action_key} - {""}
        right = self._event_object_keys(event)
        return self._weighted_jaccard(left, right, self._idf_keyword)

    @staticmethod
    def _event_numbers(event: Event) -> set[str]:
        numbers: set[str] = set()
        for article in event.articles:
            numbers |= card_keys(article.card).numbers
        return numbers

    @staticmethod
    def _number_sim(left: set[str], right: set[str]) -> tuple[float, bool]:
        if not left or not right:
            return (0.0, False)
        intersection = left & right
        if not intersection:
            return (0.0, False)
        return (len(intersection) / len(left | right), True)

    @staticmethod
    def _time_sim(
        article_time: datetime | None, event_time: datetime | None, window: int
    ) -> float:
        if not article_time or not event_time:
            return 0.5
        diff = days_between(article_time, event_time)
        if diff <= 0.5:
            return 1.0
        return max(0.0, 1.0 - diff / max(window, 1))

    # ------------------------------------------------------------ writes

    def create_event(
        self, item: CleanItem, card: EventCard, *, status: str = "developing"
    ) -> Event:
        stamp = item.published_at or now_iso()
        event = Event(
            id=f"evt_{now().strftime('%Y%m%d')}_{uuid4().hex[:8]}",
            title=item.title,
            summary=card.summary or item.summary or item.title,
            event_type=card.event_type,
            status=status,
            first_seen=stamp,
            last_seen=stamp,
            created_at=now_iso(),
            card=card,
            articles=[],
        )
        event.articles.append(self._article(item, card, None, None))
        self.add_event(event)
        return event

    def attach(
        self,
        event: Event,
        item: CleanItem,
        card: EventCard,
        score: float,
        method: str,
    ) -> None:
        event.articles.append(self._article(item, card, score, method))
        stamp = item.published_at or now_iso()
        new_stamp = parse_dt(stamp)
        last_stamp = parse_dt(event.last_seen)
        if new_stamp is not None and (last_stamp is None or new_stamp > last_stamp):
            event.last_seen = stamp
        if event.status == "needs_review" and method in ("auto", "llm"):
            event.status = "developing"
        self._index(event, card_keys(card))
        self.article_ids.add(item.material_id)
        event.refresh()

    @staticmethod
    def _article(
        item: CleanItem,
        card: EventCard,
        score: float | None,
        method: str | None,
    ) -> ArticleRef:
        return ArticleRef(
            material_id=item.material_id,
            title=item.title,
            source=item.source,
            url=item.url,
            published_at=item.published_at,
            language=item.language,
            excerpt=item.content[:1200],
            score=score,
            method=method,
            card=card,
        )

    def profile(self, event: Event) -> dict[str, Any]:
        """The compact view the verification prompt compares against."""
        articles = sorted(
            event.articles, key=lambda item: str(item.published_at or "")
        )[:5]
        return {
            "event_id": event.id,
            "title": event.title,
            "summary": event.summary,
            "event_type": event.card.event_type,
            "first_seen": event.first_seen,
            "last_seen": event.last_seen,
            "actors": sorted(event.actors),
            "locations": [item.model_dump() for item in event.locations],
            "object": event.card.object,
            "key_numbers": [fact.model_dump() for fact in event.card.key_numbers],
            "keywords": sorted(event.keywords)[:20],
            "top_articles": [
                {
                    "title": article.title,
                    "source": article.source,
                    "published_at": article.published_at,
                    "summary": article.card.summary,
                }
                for article in articles
            ],
        }

    # ------------------------------------------------------------ files

    def to_batch(self, decisions: list, warnings: list[str]) -> EventBatch:
        return EventBatch(
            generated_at=now_iso(),
            count=len(self.events),
            events=list(self.events.values()),
            decisions=decisions,
            warnings=warnings,
        )

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema": "winnie.news.event/v1",
            "generated_at": now_iso(),
            "count": len(self.events),
            "events": [event.model_dump() for event in self.events.values()],
        }
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)

    def load(self, path: str | Path) -> int:
        source = Path(path)
        if not source.is_file():
            return 0
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("ignoring unreadable event store %s: %s", source, exc)
            return 0
        if not isinstance(data, dict):
            return 0
        loaded = 0
        for raw in data.get("events") or []:
            try:
                event = Event.model_validate(raw)
            except Exception as exc:  # noqa: BLE001 - one bad event must not stop load
                logger.warning("skipping malformed event: %s", exc)
                continue
            self.add_event(event)
            loaded += 1
        logger.info(
            "loaded %d events and %d articles from %s",
            len(self.events),
            len(self.article_ids),
            source,
        )
        return loaded

    @property
    def size(self) -> int:
        return len(self.events)
