"""The six-stage pipeline.

This module only sequences: it calls the stages, persists each stage's document,
records stage status on the run, and implements the one loop the design allows —
generate then review, with a bounded retry. Business rules stay in the stage
modules; there is no news logic here.

One event failing does not fail the run. Its draft or review is kept, the event
is marked ``needs_review`` and the run finishes as ``partial`` so a person can
see exactly which stories need attention.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from winnie_news.aggregate.aggregator import aggregate_items
from winnie_news.aggregate.store import EventStore
from winnie_news.clean.cleaner import clean_materials
from winnie_news.common.timeutil import now_iso
from winnie_news.config import NewsConfig
from winnie_news.generate.generator import generate_draft
from winnie_news.ports import LLMPort, SearchPort
from winnie_news.review.reviewer import review_draft
from winnie_news.search.search import search_events, usable_hits
from winnie_news.store import NewsStore, RunRecord

logger = logging.getLogger(__name__)

STAGES = ("material", "clean", "aggregate", "search", "generate", "review")


async def run_pipeline(
    *,
    run: RunRecord,
    store: NewsStore,
    event_store: EventStore,
    llm: LLMPort,
    search: SearchPort | None,
    materials: dict,
    config: NewsConfig,
    on_update: Callable[[RunRecord], None] | None = None,
) -> RunRecord:
    """Run every stage over one material batch and return the finished run."""
    store.ensure()
    store.save_materials(run.id, materials)
    items = list(materials.get("items") or [])
    documents = list(materials.get("documents") or [])
    run.set_stage(
        "material",
        "ok",
        count=len(items),
        detail=f"{len(documents)} source documents",
    )
    _save(store, run, on_update)

    clean_batch = await _clean(run, store, items, llm, config, on_update)
    if not clean_batch.items:
        _finish(run, store, on_update, status="done", detail="no items survived cleaning")
        return run

    event_batch, touched = await _aggregate(
        run, store, event_store, clean_batch.items, llm, config, on_update
    )
    events = [
        event_store.events[event_id]
        for event_id in touched
        if event_id in event_store.events
    ]

    search_batch = await _search(run, store, events, search, config, on_update)
    hits_by_event = {
        result.event_id: usable_hits(result) for result in search_batch.events
    }

    drafts, reviews, failures = await _write_and_review(
        run, store, events, hits_by_event, llm, config, on_update
    )
    event_store.save(store.events_path)

    run.stats.update(
        {
            "materials": len(items),
            "sources": len(documents),
            "cleaned": len(clean_batch.items),
            "events": len(touched),
            "search_hits": search_batch.count,
            "drafts": len(drafts),
            "passed": sum(1 for review in reviews if review.passed),
            "needs_human": sum(
                1 for review in reviews if review.status == "needs_human"
            ),
            "failed_events": failures,
        }
    )
    status = "partial" if (failures or any(not review.passed for review in reviews)) else "done"
    _finish(
        run,
        store,
        on_update,
        status=status,
        detail=f"{len(drafts)} drafts, {run.stats['passed']} passed",
    )
    if config.keep_runs > 0:
        removed = store.prune(keep=config.keep_runs)
        if removed:
            logger.info("pruned %d old runs", len(removed))
    return run


async def _clean(
    run: RunRecord,
    store: NewsStore,
    items: list[dict],
    llm: LLMPort,
    config: NewsConfig,
    on_update: Callable[[RunRecord], None] | None,
):
    run.set_stage("clean", "running")
    _save(store, run, on_update)
    batch = await clean_materials(
        items, llm, config=config.clean, language=config.language
    )
    store.save_clean(run.id, batch)
    run.set_stage(
        "clean",
        "ok",
        count=len(batch.items),
        detail=f"{len(batch.dropped)} dropped",
    )
    _save(store, run, on_update)
    return batch


async def _aggregate(
    run: RunRecord,
    store: NewsStore,
    event_store: EventStore,
    items: list,
    llm: LLMPort,
    config: NewsConfig,
    on_update: Callable[[RunRecord], None] | None,
):
    run.set_stage("aggregate", "running")
    _save(store, run, on_update)
    batch = await aggregate_items(items, event_store, llm, config=config.aggregate)
    store.save_event_batch(run.id, batch)
    touched = {
        decision.event_id
        for decision in batch.decisions
        if decision.event_id and decision.decision != "skipped"
    }
    merged = sum(1 for decision in batch.decisions if decision.decision == "merge")
    run.set_stage(
        "aggregate",
        "ok",
        count=len(touched),
        detail=f"{merged} merged into existing events",
    )
    _save(store, run, on_update)
    return batch, touched


async def _search(
    run: RunRecord,
    store: NewsStore,
    events: list,
    provider: SearchPort | None,
    config: NewsConfig,
    on_update: Callable[[RunRecord], None] | None,
):
    run.set_stage("search", "running")
    _save(store, run, on_update)
    batch = await search_events(events, provider, config=config.search)
    store.save_search(run.id, batch)
    run.set_stage(
        "search",
        "skipped" if provider is None else "ok",
        count=batch.count,
        detail="no search provider configured" if provider is None else "",
    )
    _save(store, run, on_update)
    return batch


async def _write_and_review(
    run: RunRecord,
    store: NewsStore,
    events: list,
    hits_by_event: dict[str, list],
    llm: LLMPort,
    config: NewsConfig,
    on_update: Callable[[RunRecord], None] | None,
) -> tuple[list, list, int]:
    drafts: list = []
    reviews: list = []
    failures = 0
    requirements = config.writing.model_copy(update={"language": config.language})

    for index, event in enumerate(events):
        run.set_stage(
            "generate",
            "running",
            count=len(drafts),
            detail=f"{index + 1}/{len(events)}: {event.title[:60]}",
        )
        run.set_stage("review", "running", count=len(reviews))
        _save(store, run, on_update)
        hits = hits_by_event.get(event.id, [])
        try:
            draft, review = await _one_event(event, hits, llm, config, requirements)
        except Exception as exc:  # noqa: BLE001 - isolate one event
            failures += 1
            event.status = "needs_review"
            logger.warning("event %s failed: %s", event.id, exc)
            run.stats["last_error"] = f"{event.title}: {type(exc).__name__}: {exc}"
            continue

        store.save_draft(run.id, draft)
        store.save_review(run.id, review)
        drafts.append(draft)
        reviews.append(review)
        if review.passed:
            event.status = "ready"
        else:
            event.status = "needs_review"

    run.set_stage(
        "generate",
        "ok" if drafts or not events else "failed",
        count=len(drafts),
    )
    run.set_stage(
        "review",
        "ok",
        count=sum(1 for review in reviews if review.passed),
        detail=f"{sum(1 for review in reviews if review.passed)} passed",
    )
    _save(store, run, on_update)
    return drafts, reviews, failures


async def _one_event(event, hits, llm, config: NewsConfig, requirements):
    draft = await generate_draft(
        event,
        hits,
        llm,
        requirements=requirements,
        revision=1,
    )
    review = await review_draft(
        draft,
        event,
        hits,
        llm,
        requirements=requirements,
        retry=0,
        max_retries=config.max_review_retries,
    )
    while review.status == "retry":
        draft = await generate_draft(
            event,
            hits,
            llm,
            requirements=requirements,
            feedback=review.feedback,
            previous=draft,
            revision=draft.revision + 1,
        )
        review = await review_draft(
            draft,
            event,
            hits,
            llm,
            requirements=requirements,
            retry=review.retry + 1,
            max_retries=config.max_review_retries,
        )
    return draft, review


def _save(
    store: NewsStore,
    run: RunRecord,
    on_update: Callable[[RunRecord], None] | None,
) -> None:
    run.updated_at = now_iso()
    store.save_run(run)
    if on_update is not None:
        on_update(run)


def _finish(
    run: RunRecord,
    store: NewsStore,
    on_update: Callable[[RunRecord], None] | None,
    *,
    status: str,
    detail: str,
) -> None:
    run.status = status  # type: ignore[assignment]
    for stage in run.stages:
        if stage.status in ("pending", "running"):
            stage.status = "skipped"
            stage.detail = stage.detail or detail
    _save(store, run, on_update)
