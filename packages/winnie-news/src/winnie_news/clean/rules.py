"""Rule-based cleaning and first-pass filtering.

Rules run before any model call. They are cheap, deterministic and catch the
bulk of what a news feed contains that is not news: navigation text, adverts,
duplicate links, empty entries. Spending a model call to discover that a title
is empty is waste; more importantly, a stage that only filters with a model has
no deterministic floor when the model is unavailable.

Every rule records why it dropped an item so the console can explain a batch
that looks too small.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from winnie_news.common.material import dedup_key
from winnie_news.common.text import clean_body, clean_text

from .models import DroppedItem

# Titles that are UI furniture rather than a story.
_AD_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(点击|点此|请点击)",
        r"(广告|推广|赞助)",
        r"(下载|安装).{0,4}(app|客户端)",
        r"(登录|注册|扫码)",
        r"(更多|查看详情|阅读全文)\s*[>》»]*$",
        r"^(首页|上一页|下一页|返回)",
        r"(版权所有|免责声明|联系我们)",
        r"^(分享|收藏|评论)$",
    )
)
_URL_ONLY_RE = re.compile(r"^(https?://|www\.)\S+$", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


def material_id(item: Mapping[str, Any]) -> str:
    """A stable identity for one material item across batches."""
    return dedup_key(item)


def normalize_title(title: str) -> str:
    """Key used for cross-source duplicate detection.

    Punctuation and case differ between an agency copy and its re-publication,
    so they are removed rather than compared.
    """
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", title.lower())
    return _SPACE_RE.sub("", text)


def prepare(item: Mapping[str, Any], *, max_content_chars: int) -> dict[str, Any]:
    """Clean text fields before rules run."""
    return {
        "material_id": material_id(item),
        "title": clean_text(str(item.get("title") or "")),
        "content": clean_body(str(item.get("content") or ""), limit=max_content_chars),
        "source": clean_text(str(item.get("source") or "")),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "section": item.get("section"),
        "rank": item.get("rank"),
        "tags": list(item.get("tags") or []),
        "extra": dict(item.get("extra") or {}),
    }


def rule_filter(
    items: list[dict[str, Any]],
    *,
    min_title_chars: int = 4,
    min_total_chars: int = 12,
    max_age_days: int = 0,
    now_iso: str | None = None,
) -> tuple[list[dict[str, Any]], list[DroppedItem]]:
    """Apply the cheap filters and drop repeated URLs/titles within the batch.

    ``max_age_days`` is 0 by default because a hot list carries no date and an
    old article is still a useful event update; enable it to bound a batch by
    publication time when the sources are dated feeds.
    """
    kept: list[dict[str, Any]] = []
    dropped: list[DroppedItem] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    floor = _age_floor(max_age_days, now_iso) if max_age_days > 0 else None

    for item in items:
        title = str(item.get("title") or "")
        content = str(item.get("content") or "")
        url = str(item.get("url") or "")
        identifier = str(item.get("material_id") or "")

        def drop(reason: str) -> None:
            dropped.append(
                DroppedItem(
                    material_id=identifier,
                    title=title or None,
                    stage="rule",
                    reason=reason,
                )
            )

        if not title:
            drop("empty_title")
            continue
        if _URL_ONLY_RE.match(title):
            drop("title_is_a_link")
            continue
        if len(title) < min_title_chars:
            drop("title_too_short")
            continue
        if any(pattern.search(title) for pattern in _AD_PATTERNS):
            drop("navigation_or_advert")
            continue
        if len(title) + len(content) < min_total_chars:
            drop("too_little_text")
            continue
        if not content and not url:
            drop("no_content_and_no_link")
            continue
        if floor is not None:
            published = item.get("published_at")
            if isinstance(published, str) and published[:10] < floor:
                drop("too_old")
                continue

        if url:
            key = _canonical_url(url)
            if key in seen_urls:
                drop("duplicate_url")
                continue
            seen_urls.add(key)
        title_key = normalize_title(title)
        if title_key and title_key in seen_titles:
            drop("duplicate_title")
            continue
        seen_titles.add(title_key)
        kept.append(item)

    return kept, dropped


def _canonical_url(url: str) -> str:
    """Ignore scheme, ``www.``, query and fragment when comparing links."""
    text = url.strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    text = text.split("#", 1)[0].split("?", 1)[0]
    return text.rstrip("/")


def _age_floor(max_age_days: int, now_iso: str | None) -> str:
    from datetime import datetime, timedelta

    from winnie_news.common.timeutil import now as current

    moment = datetime.fromisoformat(now_iso) if now_iso else current()
    return (moment - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
