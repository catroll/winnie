"""Fetch RSS / Atom feeds configured in a config file and output unified JSON.

Flow:

  1. Read the config file (``rss_sources.json`` next to this module by default)
     to get the enabled feeds;
  2. Optionally expand Google News search RSS from ``google_news.keywords``;
  3. Fetch every feed concurrently and parse RSS 2.0 / RSS 1.0 (RDF) / Atom;
  4. Output JSON (``--flat`` outputs only the item array), or JSONL / CSV;
     with ``--material`` output the unified news material format
     (``winnie.news.material/v1``, see ``docs/news-material-format.md``).

One failing feed does not affect the others: the failure is recorded in
``feeds[].error``, and progress and warnings go to the module logger. stdout
carries data only. When every feed fails, the process exits non-zero.

Default output:

    {
      "source": "rss",
      "generated_at": "2026-10-21T00:30:00+08:00",
      "count": 1,
      "feeds": [
        {"id": "chinanews-scroll", "name": "中新网即时新闻", "url": "...", "count": 20}
      ],
      "items": [
        {
          "feed_id": "chinanews-scroll",
          "title": "……",
          "summary": "……",
          "source": "中新网即时新闻",
          "url": "https://……",
          "published_at": "2026-10-21T00:14:12+08:00",
          "guid": "……",
          "categories": ["即时新闻"]
        }
      ]
    }
"""

from __future__ import annotations

import argparse
import hashlib
import html.entities
import json
import logging
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable, Mapping, Sequence

from winnie_news.common.http import FetchError, fetch
from winnie_news.common.jsonio import (
    add_output_args,
    dump_csv,
    dump_json,
    dump_jsonl,
    infer_format,
    output_stream,
)
from winnie_news.common.material import dedup_key, items_from_rss, make_document
from winnie_news.common.timeutil import now_iso

logger = logging.getLogger(__name__)

GENERATOR = "winnie_news/material/rss.py"
DEFAULT_CONFIG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "rss_sources.json"
)
DEFAULT_GOOGLE_TEMPLATE = "https://news.google.com/rss/search?q={query}"
RSS_ACCEPT = "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"
CSV_FIELDS = [
    "feed_id",
    "feed_title",
    "section",
    "title",
    "summary",
    "source",
    "published_at",
    "url",
    "author",
    "guid",
    "categories",
]

XML_DECL_RE = re.compile(rb"""^\s*<\?xml[^>]*encoding\s*=\s*["']([^"']+)["']""", re.I)
XML_ENTITY_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]+);")
BARE_AMP_RE = re.compile(r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]+;)")
SLUG_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')
XML_PREDEFINED = {"amp", "lt", "gt", "quot", "apos"}


class ConfigError(RuntimeError):
    """The config file is missing, malformed, or has an invalid field."""


# ---------------------------------------------------------------- configuration


def _as_bool(value: Any, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1", "on"):
            return True
        if lowered in ("false", "no", "0", "off"):
            return False
    raise ConfigError(f"{field} must be a boolean, got {value!r}")


def _positive_float(value: Any, *, field: str, default: float) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{field} must be a number, got {value!r}") from None
    if number <= 0:
        raise ConfigError(f"{field} must be greater than 0, got {value!r}")
    return number


def _non_negative_int(value: Any, *, field: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigError(f"{field} must be a non-negative integer, got {value!r}")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ConfigError(
            f"{field} must be a non-negative integer, got {value!r}"
        ) from None
    if number < 0:
        raise ConfigError(f"{field} must be a non-negative integer, got {value!r}")
    return number


def _string_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{field} must be an array of strings")
    return [item.strip() for item in value if item.strip()]


def _string(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be a string")
    return value.strip() or None


def _positive_int(value: Any, *, field: str, default: int) -> int:
    number = _non_negative_int(value, field=field, default=default)
    if number < 1:
        raise ConfigError(f"{field} must be greater than 0, got {value!r}")
    return number


def _headers(value: Any, *, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise ConfigError(f"{field} must be an object with string keys and values")
    return {key: item for key, item in value.items()}


def _validate_url(url: str, *, field: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(f"{field} must be an http(s) URL, got {url!r}")
    return url


def slugify(value: str, *, fallback: str = "feed") -> str:
    """Compress a name into a short id/filename slug; Chinese characters are kept."""
    slug = SLUG_RE.sub("-", value).strip("-").lower()
    if slug:
        return slug[:60]
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return digest or fallback


def _normalize_defaults(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("defaults must be an object")
    return {
        "timeout": _positive_float(
            raw.get("timeout"), field="defaults.timeout", default=20.0
        ),
        "retries": _positive_int(
            raw.get("retries"), field="defaults.retries", default=3
        ),
        "max_items": _non_negative_int(
            raw.get("max_items"), field="defaults.max_items", default=0
        ),
        "workers": _positive_int(
            raw.get("workers"), field="defaults.workers", default=5
        ),
        "headers": _headers(raw.get("headers"), field="defaults.headers"),
    }


def normalize_feed(
    entry: Any, defaults: Mapping[str, Any], *, index: int
) -> dict[str, Any]:
    """Fill one config feed entry out into the internal structure."""
    if isinstance(entry, str):  # a bare URL is allowed in feeds
        entry = {"url": entry}
    if not isinstance(entry, dict):
        raise ConfigError(f"feeds[{index}] must be an object or a URL string")

    url = _string(entry.get("url"), field=f"feeds[{index}].url")
    if not url:
        raise ConfigError(f"feeds[{index}].url must not be empty")
    _validate_url(url, field=f"feeds[{index}].url")

    name = _string(entry.get("name"), field=f"feeds[{index}].name")
    feed_id = _string(entry.get("id"), field=f"feeds[{index}].id")
    if not feed_id:
        feed_id = slugify(
            name or urllib.parse.urlsplit(url).hostname or f"feed-{index}"
        )

    return {
        "id": feed_id,
        "name": name or feed_id,
        "url": url,
        "section": _string(entry.get("section"), field=f"feeds[{index}].section"),
        "language": _string(entry.get("language"), field=f"feeds[{index}].language"),
        "tags": _string_list(entry.get("tags"), field=f"feeds[{index}].tags"),
        "enabled": _as_bool(
            entry.get("enabled"), field=f"feeds[{index}].enabled", default=True
        ),
        "max_items": _non_negative_int(
            entry.get("max_items"),
            field=f"feeds[{index}].max_items",
            default=defaults["max_items"],
        ),
        "timeout": _positive_float(
            entry.get("timeout"),
            field=f"feeds[{index}].timeout",
            default=defaults["timeout"],
        ),
        "retries": _positive_int(
            entry.get("retries"),
            field=f"feeds[{index}].retries",
            default=defaults["retries"],
        ),
        "headers": {
            **defaults["headers"],
            **_headers(entry.get("headers"), field=f"feeds[{index}].headers"),
        },
        "kind": "rss",
    }


def _build_google_url(template: str, query: str, params: Mapping[str, str]) -> str:
    """Encode the keyword into the template and add any missing hl / gl / ceid."""
    url = template.replace("{query}", urllib.parse.quote_plus(query))
    missing = [
        (key, value)
        for key, value in params.items()
        if not re.search(rf"[?&]{re.escape(key)}=", url)
    ]
    if missing:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(missing)
    return url


def _normalize_google(block: Any, defaults: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand google_news.keywords into a set of feeds."""
    if block is None:
        return []
    if not isinstance(block, dict):
        raise ConfigError("google_news must be an object")
    if not _as_bool(block.get("enabled"), field="google_news.enabled", default=True):
        return []

    keywords = block.get("keywords") or []
    if not isinstance(keywords, list):
        raise ConfigError("google_news.keywords must be an array")
    if not keywords:
        return []

    template = _string(block.get("url_template"), field="google_news.url_template")
    template = template or DEFAULT_GOOGLE_TEMPLATE
    if "{query}" not in template:
        raise ConfigError(
            "google_news.url_template must contain the {query} placeholder"
        )

    params = {
        key: value
        for key in ("hl", "gl", "ceid")
        if (value := _string(block.get(key), field=f"google_news.{key}"))
    }
    base_section = _string(block.get("section"), field="google_news.section")
    base_tags = _string_list(block.get("tags"), field="google_news.tags") or [
        "Google News"
    ]
    base_max_items = _non_negative_int(
        block.get("max_items"),
        field="google_news.max_items",
        default=defaults["max_items"],
    )

    feeds: list[dict[str, Any]] = []
    for index, raw in enumerate(keywords):
        field = f"google_news.keywords[{index}]"
        if isinstance(raw, str):
            keyword = {"query": raw}
        elif isinstance(raw, dict):
            keyword = raw
        else:
            raise ConfigError(f"{field} must be a string or an object")
        if not _as_bool(keyword.get("enabled"), field=f"{field}.enabled", default=True):
            continue

        query = _string(keyword.get("query"), field=f"{field}.query")
        if not query:
            raise ConfigError(f"{field}.query must not be empty")
        name = _string(keyword.get("name"), field=f"{field}.name") or query
        feed_id = _string(keyword.get("id"), field=f"{field}.id")
        if not feed_id:
            feed_id = "google-news-" + slugify(name)

        tags = [
            *base_tags,
            *_string_list(keyword.get("tags"), field=f"{field}.tags"),
        ]
        if name not in tags:
            tags.append(name)

        feeds.append(
            {
                "id": feed_id,
                "name": f"Google 新闻：{name}",
                "url": _validate_url(
                    _build_google_url(template, query, params), field=f"{field}.url"
                ),
                "section": _string(keyword.get("section"), field=f"{field}.section")
                or base_section,
                "language": _string(keyword.get("language"), field=f"{field}.language"),
                "tags": tags,
                "enabled": True,
                "max_items": _non_negative_int(
                    keyword.get("max_items"),
                    field=f"{field}.max_items",
                    default=base_max_items,
                ),
                "timeout": _positive_float(
                    keyword.get("timeout"),
                    field=f"{field}.timeout",
                    default=defaults["timeout"],
                ),
                "retries": _positive_int(
                    keyword.get("retries"),
                    field=f"{field}.retries",
                    default=defaults["retries"],
                ),
                "headers": dict(defaults["headers"]),
                "kind": "google-news",
                "keyword": name,
            }
        )
    return feeds


def load_config(path: str) -> dict[str, Any]:
    """Read and validate the config; return {"defaults": ..., "feeds": [...]}."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file is not valid JSON: {path}: {exc}") from None

    if isinstance(raw, list):  # a bare feed array is allowed at the top level
        raw = {"feeds": raw}
    if not isinstance(raw, dict):
        raise ConfigError("config file top level must be an object or an array")

    defaults = _normalize_defaults(raw.get("defaults"))

    raw_feeds = raw.get("feeds") or []
    if not isinstance(raw_feeds, list):
        raise ConfigError("feeds must be an array")
    feeds = [
        normalize_feed(entry, defaults, index=index)
        for index, entry in enumerate(raw_feeds)
    ]
    feeds.extend(_normalize_google(raw.get("google_news"), defaults))

    seen: set[str] = set()
    for feed in feeds:
        base = feed["id"]
        suffix = 2
        while feed["id"] in seen:
            feed["id"] = f"{base}-{suffix}"
            suffix += 1
        seen.add(feed["id"])

    return {"defaults": defaults, "feeds": feeds}


def select_feeds(
    feeds: Iterable[dict[str, Any]], ids: Sequence[str] | None
) -> list[dict[str, Any]]:
    """Filter by --feed; by default keep only the feeds with enabled=true."""
    feeds = list(feeds)
    if ids:
        wanted = set(ids)
        known = {feed["id"] for feed in feeds}
        missing = sorted(wanted - known)
        if missing:
            raise ConfigError(f"no such feed id in the config: {', '.join(missing)}")
        return [feed for feed in feeds if feed["id"] in wanted]
    return [feed for feed in feeds if feed["enabled"]]


def _redacted_feed(feed: Mapping[str, Any]) -> dict[str, Any]:
    """For --list output: hide header values so Cookie / Token stay off the terminal."""
    item = dict(feed)
    if item.get("headers"):
        item["headers"] = {key: "***" for key in item["headers"]}
    return item


# ---------------------------------------------------------------- XML parsing


def _local_name(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _first_child(element: ET.Element | None, *names: str) -> ET.Element | None:
    if element is None:
        return None
    for name in names:
        for child in element:
            if _local_name(child.tag) == name:
                return child
    return None


def _element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def _attr_local(element: ET.Element, name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return value
    return None


def _element_link(element: ET.Element) -> str | None:
    """RSS's <link> text, or Atom's <link rel="alternate" href="...">."""
    links = _children(element, "link")
    fallback: str | None = None
    for link in links:
        rel = (_attr_local(link, "rel") or "").strip().lower()
        href = (link.get("href") or _element_text(link)).strip()
        if not href:
            continue
        if rel in ("", "alternate"):
            return href
        fallback = fallback or href
    return fallback


def _element_author(element: ET.Element) -> str | None:
    author = _first_child(element, "author", "creator")
    if author is None:
        return None
    name = _first_child(author, "name")
    text = _element_text(name if name is not None else author)
    return text or None


def _element_categories(element: ET.Element) -> list[str]:
    categories: list[str] = []
    for child in _children(element, "category"):
        text = _element_text(child) or (_attr_local(child, "term") or "").strip()
        if text and text not in categories:
            categories.append(text)
    return categories


def _decode_xml(raw: bytes, charset: str | None) -> str:
    declared = XML_DECL_RE.match(raw[:256])
    encoding = (
        declared.group(1).decode("ascii", "ignore") if declared else charset or "utf-8"
    )
    try:
        return raw.decode(encoding, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def _repair_entities(text: str) -> str:
    """Replace undeclared HTML entities with their characters, then escape bare &."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in XML_PREDEFINED:
            return match.group(0)
        value = html.entities.html5.get(name + ";")
        return value if value is not None else match.group(0)

    return BARE_AMP_RE.sub("&amp;", XML_ENTITY_RE.sub(replace, text))


def _load_xml(raw: bytes, charset: str | None) -> ET.Element:
    try:
        return ET.fromstring(raw)
    except ET.ParseError as first:
        repaired = _repair_entities(_decode_xml(raw, charset))
        try:
            return ET.fromstring(repaired)
        except ET.ParseError as second:
            raise ValueError(
                f"XML parse failed: {first}; still failed after repair: {second}"
            ) from second


def parse_feed_xml(raw: bytes, charset: str | None = None) -> dict[str, Any]:
    """Parse RSS 2.0 / RSS 1.0 (RDF) / Atom into the unified intermediate shape."""
    root = _load_xml(raw, charset)
    root_name = _local_name(root.tag)

    if root_name == "rss":
        channel = _first_child(root, "channel")
        if channel is None:
            channel = root
        item_elements = _children(channel, "item")
        feed_title = _element_text(_first_child(channel, "title"))
        feed_link = _element_link(channel)
        language = _element_text(_first_child(channel, "language")) or None
    elif root_name == "feed":
        channel = root
        item_elements = _children(root, "entry")
        feed_title = _element_text(_first_child(root, "title"))
        feed_link = _element_link(root)
        language = _attr_local(root, "lang")
    elif root_name == "rdf":
        channel = _first_child(root, "channel")
        if channel is None:
            channel = root
        item_elements = _children(root, "item")
        feed_title = _element_text(_first_child(channel, "title"))
        feed_link = _element_link(channel)
        language = _element_text(_first_child(channel, "language")) or None
    else:
        raise ValueError(f"unrecognised RSS root element: {root.tag!r}")

    items: list[dict[str, Any]] = []
    for element in item_elements:
        content_element = _first_child(
            element, "description", "summary", "encoded", "content"
        )
        items.append(
            {
                "id": _element_text(_first_child(element, "guid", "id"))
                or _attr_local(element, "about"),
                "title": _element_text(_first_child(element, "title")),
                "content": _element_text(content_element),
                "source": _element_text(_first_child(element, "source")),
                "url": _element_link(element),
                "published_at": _element_text(
                    _first_child(element, "pubdate", "published", "date", "updated")
                ),
                "author": _element_author(element),
                "tags": _element_categories(element),
            }
        )

    return {
        "title": feed_title,
        "link": feed_link,
        "language": language,
        "items": items,
    }


# ---------------------------------------------------------------- fetching and output


def fetch_feed(
    feed: Mapping[str, Any], *, timeout: float, retries: int
) -> dict[str, Any]:
    headers = {"Accept": RSS_ACCEPT, **feed.get("headers", {})}
    return fetch(
        feed["url"],
        headers=headers,
        timeout=timeout,
        retries=retries,
        parse=parse_feed_xml,
        label=f"[{feed['id']}] ",
    )


def collect(
    feeds: Sequence[dict[str, Any]],
    *,
    timeout: float | None,
    retries: int | None,
    limit: int | None,
    workers: int,
) -> list[dict[str, Any]]:
    """Fetch every feed concurrently; return results in config order (with failures)."""
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(feeds))) as pool:
        futures = {
            pool.submit(
                fetch_feed,
                feed,
                timeout=timeout if timeout is not None else feed.get("timeout", 20.0),
                retries=retries if retries is not None else feed.get("retries", 3),
            ): feed
            for feed in feeds
        }
        for future in as_completed(futures):
            feed = futures[future]
            try:
                parsed = future.result()
            except (FetchError, ValueError, OSError) as exc:
                logger.warning("%s collection failed: %s", feed["name"], exc)
                results[feed["id"]] = {"feed": feed, "error": str(exc), "material": []}
                continue

            material = list(
                items_from_rss(
                    parsed["items"],
                    feed_id=feed["id"],
                    feed_name=feed["name"],
                    section=feed.get("section"),
                    tags=feed.get("tags"),
                    feed_title=parsed.get("title"),
                    feed_url=feed["url"],
                    language=feed.get("language") or parsed.get("language"),
                )
            )
            max_items = feed.get("max_items", 0) if limit is None else limit
            if max_items:
                material = material[:max_items]
            logger.info("%s: %d items", feed["name"], len(material))
            results[feed["id"]] = {
                "feed": feed,
                "parsed": parsed,
                "error": None,
                "material": material,
            }
    return [results[feed["id"]] for feed in feeds]


def dedupe_material(
    items: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = dedup_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique, len(items) - len(unique)


def build_source_document(
    feed: Mapping[str, Any],
    *,
    items: Sequence[Mapping[str, Any]],
    parsed: Mapping[str, Any] | None = None,
    config_path: str = "",
    dedup: bool = False,
) -> tuple[dict[str, Any], int]:
    """Wrap one feed's material items in a unified document; return (doc, dropped)."""
    material = [dict(item) for item in items]
    removed = 0
    if dedup:
        material, removed = dedupe_material(material)

    extra: dict[str, Any] = {"source_id": feed["id"]}
    if config_path:
        extra["config"] = os.path.basename(config_path)
    parsed = parsed or {}
    if parsed.get("title"):
        extra["feed_title"] = parsed["title"]
    if feed.get("language") or parsed.get("language"):
        extra["language"] = feed.get("language") or parsed.get("language")
    if removed:
        extra["deduped"] = removed

    document = make_document(
        feed="rss",
        generator=GENERATOR,
        items=material,
        feed_url=feed["url"],
        extra=extra,
    )
    document["feed"]["name"] = feed["name"]
    return document, removed


def display_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a unified material item into a lightweight display item."""
    extra = item.get("extra") or {}
    fields = [
        ("feed_id", extra.get("feed_id")),
        ("feed_title", extra.get("feed_title")),
        ("section", item.get("section")),
        ("title", item.get("title")),
        ("summary", item.get("content")),
        ("source", item.get("source")),
        ("published_at", item.get("published_at")),
        ("url", item.get("url")),
        ("author", extra.get("author")),
        ("guid", item.get("id")),
        ("categories", extra.get("categories")),
    ]
    return {key: value for key, value in fields if value not in (None, "", [])}


def source_summary(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for result in results:
        feed = result["feed"]
        entry: dict[str, Any] = {
            "id": feed["id"],
            "name": feed["name"],
            "url": feed["url"],
            "count": len(result.get("material") or []),
        }
        if feed.get("section"):
            entry["section"] = feed["section"]
        if result.get("error"):
            entry["error"] = result["error"]
        summary.append(entry)
    return summary


def _split_filename(feed_id: str) -> str:
    safe = UNSAFE_FILENAME_RE.sub("_", feed_id).strip(" .") or "feed"
    return f"{safe}.material.json"


def write_split(
    directory: str,
    results: Sequence[Mapping[str, Any]],
    *,
    config_path: str,
    dedup: bool,
    compact: bool,
) -> int:
    os.makedirs(directory, exist_ok=True)
    written = 0
    for result in results:
        if result.get("error"):
            continue
        feed = result["feed"]
        document, _ = build_source_document(
            feed,
            items=result.get("material") or [],
            parsed=result.get("parsed"),
            config_path=config_path,
            dedup=dedup,
        )
        path = os.path.join(directory, _split_filename(feed["id"]))
        with open(path, "w", encoding="utf-8") as handle:
            dump_json(document, handle, compact=compact)
        logger.info("wrote %s", path)
        written += 1
    return written


def non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if number < 0:
        raise argparse.ArgumentTypeError("must be greater than or equal to 0")
    return number


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch RSS / Atom feeds from a config file and output unified JSON."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --list\n"
            "  %(prog)s -o rss.json\n"
            "  %(prog)s --material -o 2026-10-21.material.json\n"
            "  %(prog)s --feed chinanews-scroll --limit 10 --compact\n"
            "  %(prog)s --split-dir out/rss --material\n"
            "  %(prog)s --flat --format jsonl -o rss.jsonl\n"
        ),
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"config file path (default {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--feed",
        action="append",
        metavar="ID",
        help=(
            "only fetch the given feed id, repeatable; by default fetch every "
            "feed with enabled=true"
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help=(
            "list the expanded feeds from the config (including Google "
            "keywords) without making requests"
        ),
    )
    parser.add_argument(
        "--limit",
        type=non_negative_int,
        default=None,
        help=(
            "keep at most N items per feed (0 = no limit; defaults to "
            "max_items in the config)"
        ),
    )
    parser.add_argument(
        "--workers",
        type=non_negative_int,
        default=None,
        help=(
            "concurrent fetch count (defaults to defaults.workers in the "
            "config, 5 when unset)"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--flat",
        action="store_true",
        help=(
            "output only the item array, without the outer source / "
            "generated_at / feeds fields"
        ),
    )
    group.add_argument(
        "--material",
        action="store_true",
        help=(
            "output the unified news material format "
            "(winnie.news.material/v1, a single JSON document)"
        ),
    )
    parser.add_argument(
        "--dedup",
        action="store_true",
        help=(
            "deduplicate with winnie_news.common.material.dedup_key() "
            "(all items are kept by default)"
        ),
    )
    parser.add_argument(
        "--split-dir",
        metavar="DIR",
        help="write one <feed-id>.material.json per feed; stdout then carries no data",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="per-request timeout in seconds; defaults to the config (20 when unset)",
    )
    parser.add_argument(
        "--retries",
        type=non_negative_int,
        default=None,
        help="request retry count; defaults to the config (3 when unset)",
    )
    add_output_args(parser, formats=("json", "jsonl", "csv"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.split_dir and args.out:
        parser.error("--split-dir cannot be combined with -o/--out")
    if args.split_dir and args.flat:
        parser.error("--split-dir cannot be combined with --flat")
    if args.retries is not None and args.retries < 1:
        parser.error("--retries must be greater than 0")

    try:
        config = load_config(args.config)
        feeds = select_feeds(config["feeds"], args.feed)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list:
        dump_json(
            {
                "config": args.config,
                "defaults": config["defaults"],
                "feeds": [_redacted_feed(feed) for feed in feeds],
            },
            sys.stdout,
            compact=args.compact,
        )
        return 0

    if not feeds:
        print(
            "error: no feeds to fetch (check enabled in the config or --feed)",
            file=sys.stderr,
        )
        return 2

    workers = (
        args.workers if args.workers is not None else config["defaults"]["workers"]
    )
    if workers < 1:
        print("error: --workers must be greater than 0", file=sys.stderr)
        return 2

    results = collect(
        feeds,
        timeout=args.timeout,
        retries=args.retries,
        limit=args.limit,
        workers=workers,
    )
    succeeded = [result for result in results if not result.get("error")]
    summary = source_summary(results)

    if not succeeded:
        print("error: every feed failed, see the log above", file=sys.stderr)
        return 1

    if args.split_dir:
        written = write_split(
            args.split_dir,
            results,
            config_path=args.config,
            dedup=args.dedup,
            compact=args.compact,
        )
        if not written:
            print("error: no feed to write", file=sys.stderr)
            return 1
        logger.info("wrote %d files to %s", written, args.split_dir)
        return 0

    material: list[dict[str, Any]] = []
    for result in results:
        material.extend(result.get("material") or [])
    removed = 0
    if args.dedup:
        material, removed = dedupe_material(material)
        if removed:
            logger.info("dedup removed %d duplicate items", removed)

    fmt = args.format or infer_format(args.out)
    if fmt not in ("json", "jsonl", "csv"):
        fmt = "json"

    with output_stream(args.out) as out:
        if args.material:
            extra: dict[str, Any] = {
                "config": os.path.basename(args.config),
                "sources": summary,
            }
            if removed:
                extra["deduped"] = removed
            document = make_document(
                feed="rss",
                generator=GENERATOR,
                items=material,
                extra=extra,
            )
            dump_json(document, out, compact=args.compact)
            return 0

        items = [display_item(item) for item in material]
        if fmt == "jsonl":
            dump_jsonl(items, out)
        elif fmt == "csv":
            rows = [
                {
                    **item,
                    "categories": "|".join(item.get("categories") or []),
                }
                for item in items
            ]
            dump_csv(rows, out, CSV_FIELDS)
        else:
            document = {
                "source": "rss",
                "generated_at": now_iso(),
                "count": len(items),
                "feeds": summary,
                "items": items,
            }
            dump_json(items if args.flat else document, out, compact=args.compact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
