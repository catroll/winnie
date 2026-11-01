"""Assemble a run's reviewed drafts into a dated briefing.

The hot-news contract is mostly mechanical once every draft carries a category
and a one-line brief: select the window and the count, order newest first, group
by section, and render. Keeping the assembly here and the writing in the writer
prompt means the digest never costs a second model call and never contains a
fact the reviewed drafts do not.

Only drafts whose review passed are eligible. A digest is a publishable list,
and publishing a `needs_human` draft because it happened to be recent would
bypass the review the pipeline exists to enforce.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from winnie_news.categories import SHORT_LABELS, derive_category, normalize_category
from winnie_news.common.timeutil import now
from winnie_news.generate.models import Draft

from .models import (
    FORMATS,
    Digest,
    DigestConfig,
    DigestItem,
    DigestSection,
    empty_sections,
)

logger = logging.getLogger(__name__)

_NUMERALS = ("一", "二", "三", "四")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
# Reasons an eligible draft is not in the digest, in the order a reader cares.
EXCLUSIONS = ("needs_review", "out_of_window", "over_limit")


def build_digest(
    entries: Iterable[tuple[Draft, str]],
    events: dict[str, Any],
    *,
    config: DigestConfig | None = None,
    now_dt: datetime | None = None,
) -> Digest:
    """Build the digest for one run.

    ``entries`` pairs each draft with its review status; ``events`` maps event id
    to the stored event, whose own date is preferred over the draft's.
    """
    cfg = config or DigestConfig()
    moment = now_dt or now()
    end = moment.date()
    start = end - timedelta(days=max(0, cfg.window_days - 1))

    pairs = list(entries)
    items: list[DigestItem] = []
    excluded = {reason: 0 for reason in EXCLUSIONS}
    for draft, status in pairs:
        if status != "passed" and not cfg.include_needs_review:
            excluded["needs_review"] += 1
            continue
        event = events.get(draft.event_id)
        item_date = _item_date(draft, event, fallback=end)
        if cfg.window_days > 0 and not start <= item_date <= end:
            excluded["out_of_window"] += 1
            continue
        items.append(_item(draft, event, item_date))

    # Newest first; the sort is stable, so drafts of the same day keep the order
    # the run wrote them in.
    items.sort(key=lambda item: item.date, reverse=True)
    if cfg.max_items > 0 and len(items) > cfg.max_items:
        excluded["over_limit"] = len(items) - cfg.max_items
        items = items[: cfg.max_items]

    sections = _sections(items)
    digest = Digest(
        generated_at=moment.isoformat(timespec="seconds"),
        window_days=cfg.window_days,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        requested=cfg.max_items,
        count=len(items),
        format=cfg.format,
        sections=sections,
        excluded=excluded,
        notes=_notes(items, excluded, cfg),
    )
    logger.info(
        "digest: %d items from %d drafts (%d not passed, %d outside the window)",
        digest.count,
        len(pairs),
        excluded["needs_review"],
        excluded["out_of_window"],
    )
    return digest


def build_digest_from_store(
    store: Any,
    events: dict[str, Any],
    run_id: str,
    *,
    config: DigestConfig | None = None,
    now_dt: datetime | None = None,
) -> Digest:
    """Build from what is on disk, so a regeneration refreshes the digest.

    The store is duck-typed: this module must not import ``winnie_news.store``,
    because the store imports this one to render the digest Markdown.
    """
    statuses = {
        str(review.get("event_id") or ""): str(review.get("status") or "")
        for review in store.list_reviews(run_id)
    }
    entries = [
        (draft, statuses.get(draft.event_id, ""))
        for draft in store.load_drafts(run_id)
    ]
    return build_digest(entries, events, config=config, now_dt=now_dt)


def _item(draft: Draft, event: Any, item_date: date) -> DigestItem:
    event_type = str(getattr(getattr(event, "card", None), "event_type", "") or "")
    category = normalize_category(draft.category)
    if not category:
        locations = getattr(getattr(event, "card", None), "location", ()) or ()
        category = derive_category(event_type, locations)
    return DigestItem(
        event_id=draft.event_id,
        category=category,
        date=item_date.isoformat(),
        title=draft.title.strip(),
        brief=draft.brief.strip() or draft.title.strip(),
        tags=list(draft.tags),
        event_type=event_type,
    )


def _item_date(draft: Draft, event: Any, *, fallback: date) -> date:
    card = getattr(event, "card", None)
    time_info = getattr(card, "event_time", None)
    candidates = [
        getattr(time_info, "start", None),
        getattr(time_info, "end", None),
        getattr(event, "first_seen", None),
        draft.generated_at,
    ]
    for value in candidates:
        parsed = _parse_date(value)
        if parsed is not None:
            return parsed
    return fallback


def _parse_date(value: Any) -> date | None:
    match = _DATE_RE.match(str(value or "").strip())
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _sections(items: list[DigestItem]) -> list[DigestSection]:
    sections = empty_sections()
    by_category = {section.category: section for section in sections}
    for item in items:
        section = by_category.get(item.category)
        if section is None:
            # A category outside the fixed set can only arrive from a hand-edited
            # draft; keep the item visible rather than dropping it silently.
            section = DigestSection(category=item.category)
            sections.append(section)
            by_category[item.category] = section
        section.items.append(item)
    return sections


def _notes(
    items: list[DigestItem], excluded: dict[str, int], cfg: DigestConfig
) -> list[str]:
    """Publication text, in the same language as the drafts."""
    notes: list[str] = []
    if cfg.max_items > 0 and len(items) < cfg.max_items:
        notes.append(
            f"符合条件的热点不足 {cfg.max_items} 条，"
            f"本期实际收录 {len(items)} 条。"
        )
    if excluded.get("needs_review"):
        notes.append(f"{excluded['needs_review']} 篇稿件未通过审核，未进入汇总。")
    if excluded.get("out_of_window"):
        notes.append(
            f"{excluded['out_of_window']} 篇稿件不在最近 "
            f"{cfg.window_days} 天内，未进入汇总。"
        )
    if excluded.get("over_limit"):
        notes.append(f"另有 {excluded['over_limit']} 条超出条数上限，未收录。")
    return notes


# ---------------------------------------------------------------- rendering


def render(digest: Digest, fmt: str = "", *, hour: int | None = None) -> str:
    """Render the digest in one of the three formats the contract names."""
    chosen = (fmt or digest.format or "standard").strip().lower()
    if chosen not in FORMATS:
        raise ValueError(
            f"unknown digest format {chosen!r}; choose one of {', '.join(FORMATS)}"
        )
    if chosen == "brief":
        return _render_brief(digest, hour)
    if chosen == "table":
        return _render_table(digest)
    return _render_standard(digest)


def _render_standard(digest: Digest) -> str:
    lines = [_intro(digest), ""]
    notes = _notes_block(digest)
    if notes:
        lines.extend(notes)
        lines.append("")
    for index, section in enumerate(digest.sections):
        numeral = _NUMERALS[index] if index < len(_NUMERALS) else str(index + 1)
        lines.append(f"### {numeral}、{section.category}")
        if not section.items:
            lines.append("（本期无条目）")
        for number, item in enumerate(section.items, start=1):
            lines.append(f"{number}. **{item.title}**：{_month_day(item.date)}；{item.brief}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_brief(digest: Digest, hour: int | None) -> str:
    moment_hour = now().hour if hour is None else hour
    greeting = "早安" if moment_hour < 12 else "晚安"
    lines = [
        f"📰 {greeting}，以下是今日要闻速览（{_cn_date(digest.end_date)}）：",
        "",
    ]
    for section in digest.sections:
        if not section.items:
            continue
        label = SHORT_LABELS.get(section.category, section.category)
        lines.append(f"【{label}】")
        for number, item in enumerate(section.items, start=1):
            lines.append(f"{number}. {item.brief}")
        lines.append("")
    notes = _notes_block(digest)
    if notes:
        lines.extend(notes)
    return "\n".join(lines).rstrip() + "\n"


def _render_table(digest: Digest) -> str:
    lines = [_intro(digest), ""]
    lines.append("| 序号 | 日期 | 分类 | 新闻标题 | 核心要点 |")
    lines.append("|------|------|------|----------|----------|")
    ordered = sorted(digest.items(), key=lambda item: item.date, reverse=True)
    for number, item in enumerate(ordered, start=1):
        label = SHORT_LABELS.get(item.category, item.category)
        lines.append(
            f"| {number} | {_month_day(item.date)} | {label} | "
            f"{_cell(item.title)} | {_cell(item.brief)} |"
        )
    notes = _notes_block(digest)
    if notes:
        lines.append("")
        lines.extend(notes)
    return "\n".join(lines).rstrip() + "\n"


def _intro(digest: Digest) -> str:
    if digest.start_date == digest.end_date:
        window = f"**{_cn_date(digest.end_date)}**"
        span = ""
    else:
        window = f"**{_cn_date(digest.start_date)}—{_cn_month_day(digest.end_date)}**"
        span = f"近{digest.window_days}天的 "
    return (
        f"以下为 {window} {span}{digest.count} 条热点新闻"
        "（国内 / 国际 / 财经科技 / 体育文娱混合，按时间线整理）："
    )


def _notes_block(digest: Digest) -> list[str]:
    return list(digest.notes)


def _cn_date(value: str) -> str:
    parsed = _parse_date(value)
    if parsed is None:
        return value
    return f"{parsed.year}年{parsed.month}月{parsed.day}日"


def _month_day(value: str) -> str:
    parsed = _parse_date(value)
    if parsed is None:
        return value
    return f"{parsed.month:02d}/{parsed.day:02d}"


def _cn_month_day(value: str) -> str:
    """The hot-news standard header's end date: MM月DD日, no year."""
    parsed = _parse_date(value)
    if parsed is None:
        return value
    return f"{parsed.month}月{parsed.day:02d}日"


def _cell(value: str) -> str:
    return " ".join(str(value or "").replace("|", "／").split())
