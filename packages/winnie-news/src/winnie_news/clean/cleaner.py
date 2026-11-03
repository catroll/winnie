"""The clean stage: rules first, then extraction, then a second filter.

Two filtering passes exist for a reason. The rule pass is cheap and decides what
deserves a model call; the second pass uses the extraction and decides whether
the item is news the pipeline can write from. Skipping either one either wastes
tokens or lets thin items reach the draft.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from winnie_news.common.timeutil import now_iso
from winnie_news.ports import LLMError, LLMPort, raise_if_balance_exhausted

from . import extract as extractor
from .models import CleanBatch, CleanItem, DroppedItem
from .rules import normalize_title, prepare, rule_filter

logger = logging.getLogger(__name__)


class CleanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A news body longer than this is truncated before the model sees it. The
    # full text is kept in the clean document for the writing stage.
    max_content_chars: int = 6000
    min_title_chars: int = 4
    min_total_chars: int = 12
    # 0 disables the publication-age filter (hot lists carry no date).
    max_age_days: int = 0
    min_quality_score: float = 0.3
    max_items: int = 200
    concurrency: int = 3


async def clean_materials(
    items: list[dict],
    llm: LLMPort,
    *,
    config: CleanConfig | None = None,
    language: str = "zh",
    now: str | None = None,
) -> CleanBatch:
    """Clean and extract a material item list into ``winnie.news.clean/v1``."""
    cfg = config or CleanConfig()
    warnings: list[str] = []

    prepared = [prepare(item, max_content_chars=cfg.max_content_chars) for item in items]
    kept, dropped = rule_filter(
        prepared,
        min_title_chars=cfg.min_title_chars,
        min_total_chars=cfg.min_total_chars,
        max_age_days=cfg.max_age_days,
        now_iso=now,
    )
    if len(kept) > cfg.max_items:
        warnings.append(
            f"kept the first {cfg.max_items} of {len(kept)} items; "
            "raise clean.max_items to process more"
        )
        kept = _prioritise(kept)[: cfg.max_items]
    else:
        kept = _prioritise(kept)

    extracted, extract_dropped, errors = await _extract_all(
        kept, llm, config=cfg, language=language
    )
    dropped.extend(extract_dropped)
    if kept and errors == len(kept):
        raise LLMError(
            "every material item failed extraction; check the news.extract "
            "binding and the provider key before rerunning"
        )

    items_out, filtered = _second_filter(
        extracted, min_score=cfg.min_quality_score, language=language
    )
    dropped.extend(filtered)
    batch = CleanBatch(
        generated_at=now_iso(),
        items=items_out,
        dropped=dropped,
        warnings=warnings,
    )
    logger.info(
        "clean: %d materials -> %d kept, %d dropped, %d extraction errors",
        len(items),
        len(items_out),
        len(dropped),
        errors,
    )
    return batch


async def _extract_all(
    items: list[dict],
    llm: LLMPort,
    *,
    config: CleanConfig,
    language: str,
) -> tuple[list[CleanItem], list[DroppedItem], int]:
    semaphore = asyncio.Semaphore(max(1, config.concurrency))
    results: list[CleanItem | DroppedItem] = []

    async def one(item: dict) -> None:
        async with semaphore:
            try:
                extraction = await extractor.extract(item, llm)
                results.append(extractor.to_clean_item(item, extraction, language=language))
            except Exception as exc:  # noqa: BLE001 - isolate one item
                raise_if_balance_exhausted(exc)
                logger.warning("extraction failed for %s: %s", item.get("url"), exc)
                results.append(
                    DroppedItem(
                        material_id=str(item.get("material_id") or ""),
                        title=str(item.get("title") or "") or None,
                        stage="llm",
                        reason=f"extraction_error: {type(exc).__name__}: {exc}",
                    )
                )

    await asyncio.gather(*(one(item) for item in items))
    cleaned = [item for item in results if isinstance(item, CleanItem)]
    dropped = [item for item in results if isinstance(item, DroppedItem)]
    return cleaned, dropped, len(dropped)


def _second_filter(
    items: list[CleanItem], *, min_score: float, language: str
) -> tuple[list[CleanItem], list[DroppedItem]]:
    kept: list[CleanItem] = []
    dropped: list[DroppedItem] = []
    seen_titles: set[str] = set()

    for item in items:
        if not item.quality.keep:
            dropped.append(
                DroppedItem(
                    material_id=item.material_id,
                    title=item.title,
                    stage="llm",
                    reason=f"not_news: {item.quality.reason or 'extractor rejected it'}",
                )
            )
            continue
        if item.quality.score < min_score:
            dropped.append(
                DroppedItem(
                    material_id=item.material_id,
                    title=item.title,
                    stage="llm",
                    reason=f"low_quality: score {item.quality.score:.2f} < {min_score:.2f}",
                )
            )
            continue
        title_key = normalize_title(item.title)
        if title_key and title_key in seen_titles:
            dropped.append(
                DroppedItem(
                    material_id=item.material_id,
                    title=item.title,
                    stage="llm",
                    reason="duplicate_title_after_extraction",
                )
            )
            continue
        seen_titles.add(title_key)
        kept.append(item)
    return kept, dropped


def _prioritise(items: list[dict]) -> list[dict]:
    """Ranked hot-list entries first, then newest published, then input order."""

    def key(indexed: tuple[int, dict]) -> tuple[int, float, int]:
        index, item = indexed
        rank = item.get("rank")
        rank_key = rank if isinstance(rank, int) and rank > 0 else 10**6
        return (rank_key, -_epoch(item.get("published_at")), index)

    return [item for _, item in sorted(enumerate(items), key=key)]


def _epoch(value: object) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0
