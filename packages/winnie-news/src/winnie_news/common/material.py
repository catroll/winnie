"""The ``winnie.news.material/v1`` contract.

A material document is one source's batch of items. Items have a fixed field
set: an absent value is written as ``null``/``[]``/``{}`` rather than omitted,
so a consumer never has to guess between "missing" and "empty". Source-specific
fields live in ``extra``; common fields never vary by source.

Layout::

    {
      "schema": "winnie.news.material/v1",
      "feed": {"id", "name", "url", "generator"},
      "fetched_at": "2026-10-21T01:30:00+08:00",
      "count": 12,
      "items": [item, ...],
      "extra": {}
    }

Item::

    {
      "id": null, "title": "", "content": "", "source": "",
      "url": null, "published_at": null, "section": null, "rank": null,
      "tags": [], "extra": {}
    }
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Iterator, Mapping, Sequence

from winnie_news import MATERIAL_SCHEMA

from .text import clean_text
from .timeutil import normalize_time, now_iso

# Registered feeds. Adding a source means adding its id here and documenting its
# field mapping; an unregistered feed is a programming error, not a warning,
# because a typo in the id would otherwise silently produce an unlabelled batch.
FEEDS: dict[str, dict[str, str | None]] = {
    "tophub-daily": {
        "name": "tophub.today daily digest",
        "url": "https://tophub.today/daily",
    },
    "tophub-node-items": {
        "name": "tophub.today node items",
        "url": "https://tophub.today/node-items-by-date",
    },
    "tophub-node-page": {
        "name": "tophub.today node page",
        "url": "https://tophub.today/",
    },
    "tencent-news-hot": {
        "name": "Tencent News hot list",
        "url": "https://news.qq.com/",
    },
    "rss": {
        "name": "RSS feeds",
        "url": None,
    },
}

ITEM_FIELDS = (
    "id",
    "title",
    "content",
    "source",
    "url",
    "published_at",
    "section",
    "rank",
    "tags",
    "extra",
)


def _text(value: Any) -> str:
    return clean_text(str(value)) if value is not None else ""


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _rank(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _unique_texts(values: Iterable[Any]) -> list[str]:
    """Drop empties and duplicates while keeping order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def make_item(
    *,
    title: Any,
    content: Any = "",
    source: Any = "",
    url: Any = None,
    published_at: Any = None,
    item_id: Any = None,
    section: Any = None,
    rank: Any = None,
    tags: Sequence[str] | None = None,
    extra: Mapping[str, Any] | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """Build one item with every field present."""
    return {
        "id": _optional_text(item_id),
        "title": _text(title),
        "content": _text(content),
        "source": _text(source),
        "url": _optional_text(url),
        "published_at": normalize_time(published_at, date=date),
        "section": _optional_text(section),
        "rank": _rank(rank),
        "tags": [str(tag) for tag in (tags or [])],
        "extra": dict(extra or {}),
    }


def make_document(
    *,
    feed: str,
    generator: str,
    items: Iterable[Mapping[str, Any]],
    feed_url: str | None = None,
    extra: Mapping[str, Any] | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Wrap items in a material document; ``feed`` must be registered."""
    if feed not in FEEDS:
        raise KeyError(f"unregistered feed {feed!r}; add it to FEEDS first")
    material = [dict(item) for item in items]
    meta = FEEDS[feed]
    return {
        "schema": MATERIAL_SCHEMA,
        "feed": {
            "id": feed,
            "name": meta["name"],
            "url": feed_url or meta.get("url"),
            "generator": generator,
        },
        "fetched_at": normalize_time(fetched_at) or now_iso(),
        "count": len(material),
        "items": material,
        "extra": dict(extra or {}),
    }


# ---------------------------------------------------------------- converters


def items_from_tophub_daily(
    sections: Iterable[Mapping[str, Any]],
) -> Iterator[dict[str, Any]]:
    """Convert ``[{title, items: [{title, summary}]}]`` sections."""
    for section in sections:
        section_name = _optional_text(section.get("title"))
        for raw in section.get("items") or []:
            title = _text(raw.get("title"))
            if not title:
                continue
            yield make_item(
                title=title,
                content=raw.get("summary"),
                source="tophub.today",
                section=section_name,
            )


def items_from_node_items(
    raw_items: Iterable[Mapping[str, Any]],
    *,
    date: str,
    nodeid: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Convert ``node-items-by-date`` entries."""
    for raw in raw_items:
        title = _text(raw.get("title"))
        if not title:
            continue
        extra = {
            "heat": raw.get("extra"),
            "domain": raw.get("domain"),
            "md5": raw.get("md5"),
            "thumbnail": raw.get("thumbnail"),
        }
        if nodeid:
            extra["nodeid"] = str(nodeid)
        yield make_item(
            item_id=raw.get("ID"),
            title=title,
            content=raw.get("description"),
            source=raw.get("sitename") or "tophub.today",
            url=raw.get("url"),
            published_at=raw.get("time"),
            date=date,
            extra={k: v for k, v in extra.items() if v not in (None, "")},
        )


def items_from_node_page(
    raw_items: Iterable[Mapping[str, Any]],
    *,
    source: str,
    nodeid: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Convert parsed node-page rows."""
    for raw in raw_items:
        title = _text(raw.get("title"))
        if not title:
            continue
        extra: dict[str, Any] = {"heat": raw.get("heat")}
        if nodeid:
            extra["nodeid"] = str(nodeid)
        yield make_item(
            item_id=raw.get("id"),
            title=title,
            source=source,
            url=raw.get("url"),
            rank=raw.get("rank"),
            extra={k: v for k, v in extra.items() if v not in (None, "")},
        )


def items_from_tencent_hot(
    document: Mapping[str, Any],
) -> Iterator[dict[str, Any]]:
    """Convert a parsed Tencent hot list."""
    for raw in document.get("items") or []:
        title = _text(raw.get("title"))
        if not title:
            continue
        yield make_item(
            title=title,
            content=raw.get("summary"),
            source=raw.get("media") or "Tencent News",
            url=raw.get("url"),
            published_at=raw.get("published_at"),
            rank=raw.get("rank"),
        )


def items_from_rss(
    raw_items: Iterable[Mapping[str, Any]],
    *,
    feed_id: str,
    feed_name: str,
    section: str | None = None,
    tags: Sequence[str] | None = None,
    feed_title: str | None = None,
    feed_url: str | None = None,
    language: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Convert items parsed from an RSS/Atom feed."""
    base_tags = _unique_texts(tags or [])
    for raw in raw_items:
        title = _text(raw.get("title"))
        if not title:
            continue
        categories = _unique_texts(raw.get("tags") or [])
        extra: dict[str, Any] = {
            "feed_id": feed_id,
            "feed_title": _optional_text(feed_title),
            "feed_url": _optional_text(feed_url),
            "language": _optional_text(language),
            "author": _optional_text(raw.get("author")),
            "categories": categories,
        }
        yield make_item(
            item_id=raw.get("id"),
            title=title,
            content=raw.get("content"),
            source=raw.get("source") or feed_name,
            url=raw.get("url"),
            published_at=raw.get("published_at"),
            section=section or raw.get("section") or feed_title or feed_name,
            tags=base_tags + categories,
            extra={k: v for k, v in extra.items() if v not in (None, "", [])},
        )


# ---------------------------------------------------------------- dedup/check


def dedup_key(item: Mapping[str, Any]) -> str:
    """Dedup key: id+source, else url, else a hash of title/source/time."""
    item_id = item.get("id")
    source = item.get("source") or ""
    if item_id:
        return f"id:{source}:{item_id}"
    url = item.get("url")
    if url:
        return f"url:{url}"
    basis = "\x1f".join(
        str(item.get(key) or "") for key in ("title", "source", "published_at")
    )
    return "hash:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _validate_item(index: int, item: Any) -> list[str]:
    prefix = f"items[{index}]"
    if not isinstance(item, dict):
        return [f"{prefix} must be an object"]

    problems: list[str] = []
    missing = [field for field in ITEM_FIELDS if field not in item]
    if missing:
        problems.append(f"{prefix} is missing fields: {', '.join(missing)}")

    if not isinstance(item.get("title"), str) or not item.get("title").strip():
        problems.append(f"{prefix}.title must be a non-empty string")
    if not isinstance(item.get("content"), str):
        problems.append(f"{prefix}.content must be a string")
    if not isinstance(item.get("source"), str) or not item.get("source").strip():
        problems.append(f"{prefix}.source must be a non-empty string")
    for field in ("id", "url", "published_at", "section"):
        if item.get(field) is not None and not isinstance(item.get(field), str):
            problems.append(f"{prefix}.{field} must be a string or null")
    rank = item.get("rank")
    if rank is not None and (
        not isinstance(rank, int) or isinstance(rank, bool) or rank < 1
    ):
        problems.append(f"{prefix}.rank must be an integer >= 1 or null")
    tags = item.get("tags")
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        problems.append(f"{prefix}.tags must be an array of strings")
    if not isinstance(item.get("extra"), dict):
        problems.append(f"{prefix}.extra must be an object")
    return problems


def validate_document(document: Any) -> list[str]:
    """Validate a material document; an empty list means it is valid."""
    if not isinstance(document, dict):
        return ["document must be a JSON object"]

    problems: list[str] = []
    if document.get("schema") != MATERIAL_SCHEMA:
        problems.append(f"schema must be {MATERIAL_SCHEMA!r}")

    feed = document.get("feed")
    if not isinstance(feed, dict):
        problems.append("feed must be an object")
    else:
        for field in ("id", "name", "generator"):
            if not isinstance(feed.get(field), str) or not feed.get(field).strip():
                problems.append(f"feed.{field} must be a non-empty string")
        if feed.get("url") is not None and not isinstance(feed.get("url"), str):
            problems.append("feed.url must be a string or null")

    if not isinstance(document.get("fetched_at"), str) or not document.get(
        "fetched_at"
    ):
        problems.append("fetched_at must be a non-empty string")

    items = document.get("items")
    if not isinstance(items, list):
        problems.append("items must be an array")
        items = []
    else:
        for index, item in enumerate(items):
            problems.extend(_validate_item(index, item))

    if document.get("count") != len(items):
        problems.append(f"count should be {len(items)}, got {document.get('count')!r}")
    if "extra" in document and not isinstance(document.get("extra"), dict):
        problems.append("extra must be an object")
    return problems


def flatten(documents: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Flatten documents into one list, tagging each item with its origin.

    The extra fields are what later stages use to say *where* a claim came from
    without carrying the document around.
    """
    items: list[dict[str, Any]] = []
    for document in documents:
        feed = document.get("feed") or {}
        for item in document.get("items") or []:
            copy = dict(item)
            extra = dict(item.get("extra") or {})
            extra.setdefault("source_feed_id", feed.get("id"))
            extra.setdefault("source_feed_name", feed.get("name"))
            copy["extra"] = extra
            items.append(copy)
    return items
