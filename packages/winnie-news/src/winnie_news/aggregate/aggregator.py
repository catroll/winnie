"""The aggregate stage: cards, recall, scoring and a bounded LLM review.

Ordering: cards are extracted concurrently (independent calls), then applied to
the store one article at a time in input order. Matching is sequential because
each merge changes the store the next comparison sees — the same article could
otherwise match two events that only exist because they were processed in
parallel.

Decisions are recorded per article so the console can answer "why did this land
on an existing event" without re-running the scorer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from winnie_news.clean.models import CleanItem
from winnie_news.common.timeutil import now_iso
from winnie_news.ports import LLMError, LLMPort, raise_if_balance_exhausted

from .cards import extract_card, verify_candidate
from .models import AggregateDecision, EventBatch, EventCard
from .store import AggregateConfig, Candidate, EventStore

logger = logging.getLogger(__name__)

Decision = Literal["new", "merge", "review", "skipped"]


async def aggregate_items(
    items: list[CleanItem],
    store: EventStore,
    llm: LLMPort,
    *,
    config: AggregateConfig | None = None,
) -> EventBatch:
    """Assign every item to a new or existing event and return the batch."""
    cfg = config or AggregateConfig()
    cards, failures = await _extract_cards(items, llm, concurrency=cfg.verify_concurrency)
    if items and len(failures) == len(items):
        first = failures[0]
        raise LLMError(
            "every event card failed to extract: "
            f"{first.get('error') if first else 'unknown error'}; check the "
            "news.extract binding before rerunning"
        )

    decisions: list[AggregateDecision] = []
    warnings: list[str] = []

    for item in items:
        card = cards.get(item.material_id)
        if card is None:
            failure = next(
                (entry for entry in failures if entry["material_id"] == item.material_id),
                {},
            )
            decisions.append(
                AggregateDecision(
                    material_id=item.material_id,
                    decision="skipped",
                    method="extract",
                    reason=str(failure.get("error") or "no event card"),
                )
            )
            warnings.append(
                f"skipped {item.title!r}: {failure.get('error') or 'no event card'}"
            )
            continue

        decision = await _assign(item, card, store, llm, cfg)
        decisions.append(decision)

    return store.to_batch(decisions, warnings)


async def _extract_cards(
    items: list[CleanItem], llm: LLMPort, *, concurrency: int
) -> tuple[dict[str, EventCard], list[dict[str, str]]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    cards: dict[str, EventCard] = {}
    failures: list[dict[str, str]] = []

    async def one(item: CleanItem) -> None:
        async with semaphore:
            try:
                cards[item.material_id] = await extract_card(item, llm)
            except Exception as exc:  # noqa: BLE001 - isolate one article
                raise_if_balance_exhausted(exc)
                logger.warning("card extraction failed for %s: %s", item.url, exc)
                failures.append(
                    {
                        "material_id": item.material_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

    await asyncio.gather(*(one(item) for item in items))
    return cards, failures


async def _assign(
    item: CleanItem,
    card: EventCard,
    store: EventStore,
    llm: LLMPort,
    config: AggregateConfig,
) -> AggregateDecision:
    if item.material_id in store.article_ids:
        # A second run over an overlapping material batch must not attach the
        # same article twice; the event already carries it.
        return AggregateDecision(
            material_id=item.material_id,
            decision="skipped",
            method="duplicate",
            reason="article is already part of an event",
        )

    published_at = card.event_time.start or item.published_at
    candidates = store.recall(card, published_at, config)
    matches: list[Candidate] = []
    for event_id in candidates:
        event = store.events.get(event_id)
        if event is None:
            continue
        candidate = store.score_candidate(card, event, published_at, config)
        if candidate is not None:
            matches.append(candidate)

    if not matches:
        event = store.create_event(item, card)
        return AggregateDecision(
            material_id=item.material_id,
            event_id=event.id,
            decision="new",
            method="no_candidate",
            reason="no event in the store passed the hard filters",
        )

    best = max(matches, key=lambda candidate: candidate.score)
    event = store.events[best.event_id]

    if best.score >= config.auto_threshold and best.anchor:
        store.attach(event, item, card, best.score, "auto")
        return AggregateDecision(
            material_id=item.material_id,
            event_id=event.id,
            decision="merge",
            method="auto",
            score=best.score,
            reason=_details(best),
        )

    if best.score >= config.gray_threshold:
        profile = store.profile(event)
        verdict = await verify_candidate(card, profile, llm)
        if verdict.decision == "same":
            store.attach(event, item, card, best.score, "llm")
            return AggregateDecision(
                material_id=item.material_id,
                event_id=event.id,
                decision="merge",
                method="llm",
                score=best.score,
                reason=f"{_details(best)}; llm: {verdict.reason}",
            )
        if verdict.decision == "uncertain":
            created = store.create_event(item, card, status="needs_review")
            return AggregateDecision(
                material_id=item.material_id,
                event_id=created.id,
                decision="review",
                method="llm",
                score=best.score,
                reason=(
                    f"gray zone ({_details(best)}) and the reviewer was unsure: "
                    f"{verdict.reason}"
                ),
            )
        created = store.create_event(item, card)
        return AggregateDecision(
            material_id=item.material_id,
            event_id=created.id,
            decision="new",
            method="llm",
            score=best.score,
            reason=f"gray zone but different: {verdict.reason}",
        )

    created = store.create_event(item, card)
    return AggregateDecision(
        material_id=item.material_id,
        event_id=created.id,
        decision="new",
        method="below_threshold",
        score=best.score,
        reason=_details(best),
    )


def _details(candidate: Candidate) -> str:
    parts = ", ".join(f"{key}={value:.2f}" for key, value in candidate.details.items())
    return f"score={candidate.score:.2f} ({parts})"


def new_batch(warnings: list[str] | None = None) -> EventBatch:
    return EventBatch(generated_at=now_iso(), warnings=list(warnings or []))
