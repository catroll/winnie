"""Unified news material fetching interface.

One function returns ``winnie.news.material/v1`` documents for every source:

  - ``rss``: one document and one cache file per feed in ``rss_sources.json``;
  - ``tencent``: ``tencent-news-cli hot``;
  - ``tophub-daily``: the tophub.today daily digest;
  - ``tophub-node-items``: tophub node list items (for example the Zhihu hot
    list);
  - ``tophub-node-page``: a tophub node page (disabled by default so that
    node-items does not fetch the same list twice).

Cache rules:

  - directory: ``news/material/.cache/material/<YYYY>/<MMDD>/`` by default;
  - file: ``<source type>_<source name>_<HHMMSS>.json``, whose content is the
    material document itself;
  - lifetime: one hour by default. For each source the newest cache file is
    checked first and returned directly while its mtime is within the lifetime,
    without contacting the origin;
  - within one batch the same RSS URL is requested only once; every RSS feed
    still writes its own file;
  - the cache keeps a few recent historical files and never deletes them
    automatically.

Public entry point:

    from winnie_news.material.fetcher import get_all_materials

    batch = get_all_materials()
    documents = batch["documents"]   # one material document per source
    items = batch["items"]           # every item, with source_type/name/id in extra

Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import sys
import tempfile
import threading
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from winnie_news.common.jsonio import add_output_args, dump_json, output_stream
from winnie_news.common.material import (
    items_from_node_items,
    items_from_node_page,
    items_from_rss,
    items_from_tencent_hot,
    items_from_tophub_daily,
    make_document,
    validate_document,
)
from winnie_news.common.text import strip_tags
from winnie_news.common.timeutil import now, now_iso, today
from winnie_news.material import (
    rss,
    tencent,
    tophub_daily,
    tophub_node_items,
    tophub_node_page,
)

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().parent / "sources.json"
DEFAULT_RSS_CONFIG = Path(__file__).resolve().parent / "rss_sources.json"
DEFAULT_MAX_AGE = 3600.0
SUPPORTED_TYPES = (
    "tencent",
    "tophub-daily",
    "tophub-node-items",
    "tophub-node-page",
)
TYPE_ALIASES = {
    "tophub": "tophub-daily",
    "tencent-news": "tencent",
    "tencent-news-hot": "tencent",
}
SAFE_NAME_RE = re.compile(r'[\\/:*?"<>|：\s\[\]]+')
DEFAULT_NAMES = {
    "tencent": "腾讯新闻热点榜",
    "tophub-daily": "每日早晚报",
    "tophub-node-page": "tophub节点页",
}
DEFAULT_TIMEOUTS = {
    "tencent": 30.0,
    "tophub-daily": 20.0,
    "tophub-node-items": 20.0,
    "tophub-node-page": 20.0,
}

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_RSS_LOCK = threading.Lock()


class MaterialConfigError(RuntimeError):
    """The material configuration is missing or has an invalid field."""


class MaterialFetchError(RuntimeError):
    """One source failed to fetch (internal; never raised for a whole batch)."""


# ---------------------------------------------------------------- time and cache


def _now_shanghai() -> datetime:
    """Current time in Asia/Shanghai."""
    return now()


def _sanitize(value: Any, *, fallback: str = "source", limit: int = 80) -> str:
    text = SAFE_NAME_RE.sub("-", str(value or "")).strip(" .-")
    text = re.sub(r"-{2,}", "-", text)
    if not text:
        text = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:8] or fallback
    return text[:limit]


def _cache_directories(root: Path, now: datetime, max_age: float) -> list[Path]:
    days = max(1, int(math.ceil(max(0.0, max_age) / 86400)) + 1)
    return [
        root
        / (now - timedelta(days=offset)).strftime("%Y")
        / (now - timedelta(days=offset)).strftime("%m%d")
        for offset in range(days)
    ]


def _read_cache(
    cache_key: str,
    *,
    root: Path,
    max_age: float,
    now: datetime,
    force: bool = False,
) -> tuple[dict[str, Any], Path, float] | None:
    """Return ``(document, path, age_seconds)``, or None without a valid cache."""
    if force or max_age <= 0:
        return None

    now_ts = now.timestamp()
    candidates: list[tuple[float, Path, float]] = []
    for directory in _cache_directories(root, now, max_age):
        if not directory.is_dir():
            continue
        for path in directory.glob(f"{cache_key}_*.json"):
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            age = now_ts - mtime
            if age <= max_age:
                candidates.append((mtime, path, max(0.0, age)))

    for _, path, age in sorted(candidates, reverse=True):
        try:
            with path.open(encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("ignoring unreadable cache %s: %s", path, exc)
            continue
        problems = validate_document(document)
        if problems:
            logger.warning("ignoring invalid cache %s: %s", path, problems[0])
            continue
        return document, path, age
    return None


def _write_cache(
    cache_key: str,
    document: Mapping[str, Any],
    *,
    root: Path,
    now: datetime,
) -> Path:
    problems = validate_document(document)
    if problems:
        raise MaterialFetchError(f"material document failed validation: {problems[0]}")

    directory = root / now.strftime("%Y") / now.strftime("%m%d")
    directory.mkdir(parents=True, exist_ok=True)
    base = f"{cache_key}_{now.strftime('%H%M%S')}"
    path = directory / f"{base}.json"
    suffix = 2
    while path.exists():
        path = directory / f"{base}-{suffix}.json"
        suffix += 1

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=directory,
        prefix=".tmp-",
        suffix=".json",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            dump_json(document, handle)
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return path


def _lock_for(cache_key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(cache_key, threading.Lock())


# ---------------------------------------------------------------- configuration


def _as_bool(value: Any, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1", "on"):
            return True
        if lowered in ("false", "no", "0", "off"):
            return False
    raise MaterialConfigError(f"{field} must be a boolean, got {value!r}")


def _positive_float(value: Any, *, field: str, default: float) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MaterialConfigError(f"{field} must be a number, got {value!r}") from None
    if not math.isfinite(number) or number <= 0:
        raise MaterialConfigError(
            f"{field} must be a finite positive number, got {value!r}"
        )
    return number


def _non_negative_float(value: Any, *, field: str, default: float) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MaterialConfigError(f"{field} must be a number, got {value!r}") from None
    if not math.isfinite(number) or number < 0:
        raise MaterialConfigError(
            f"{field} must be a finite non-negative number, got {value!r}"
        )
    return number


def _positive_int(value: Any, *, field: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise MaterialConfigError(
            f"{field} must be a positive integer, got {value!r}"
        )
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise MaterialConfigError(
            f"{field} must be a positive integer, got {value!r}"
        ) from None
    if number < 1:
        raise MaterialConfigError(f"{field} must be greater than 0, got {value!r}")
    return number


def _non_negative_int(value: Any, *, field: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise MaterialConfigError(
            f"{field} must be a non-negative integer, got {value!r}"
        )
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise MaterialConfigError(
            f"{field} must be a non-negative integer, got {value!r}"
        ) from None
    if number < 0:
        raise MaterialConfigError(
            f"{field} must be a non-negative integer, got {value!r}"
        )
    return number


def _string(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MaterialConfigError(f"{field} must be a string")
    return value.strip() or None


def _normalize_type(value: Any, *, field: str = "type", allow_rss: bool = False) -> str:
    name = str(value or "").strip().lower().replace("_", "-")
    name = TYPE_ALIASES.get(name, name)
    allowed = SUPPORTED_TYPES + (("rss",) if allow_rss else ())
    if name not in allowed:
        raise MaterialConfigError(
            f"{field} does not support {value!r}; choose from: {', '.join(allowed)}"
        )
    return name


def _validate_date(value: str | None, *, field: str) -> str | None:
    if not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise MaterialConfigError(
            f"{field} must be YYYY-MM-DD, got {value!r}"
        ) from None
    return value


def _validate_url(value: str, *, field: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise MaterialConfigError(
            f"{field} must be an http(s) URL, got {value!r}"
        )
    return value


def _normalize_registry_entry(entry: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise MaterialConfigError(f"sources[{index}] must be an object")

    source_type = _normalize_type(entry.get("type"), field=f"sources[{index}].type")
    field = f"sources[{index}]"
    name = _string(entry.get("name"), field=f"{field}.name") or DEFAULT_NAMES.get(
        source_type, source_type
    )
    nodeid = entry.get("nodeid")
    if nodeid is not None:
        nodeid = str(nodeid).strip() or None
    if source_type == "tophub-node-items" and not nodeid:
        raise MaterialConfigError(f"{field}.nodeid must not be empty")

    source_id = _string(entry.get("id"), field=f"{field}.id") or _sanitize(
        f"{source_type}-{nodeid or name}"
    )

    normalized: dict[str, Any] = {
        "type": source_type,
        "id": source_id,
        "name": name,
        "enabled": _as_bool(
            entry.get("enabled"), field=f"{field}.enabled", default=True
        ),
        "timeout": _positive_float(
            entry.get("timeout"),
            field=f"{field}.timeout",
            default=DEFAULT_TIMEOUTS[source_type],
        ),
    }

    if source_type == "tencent":
        normalized["limit"] = _positive_int(
            entry.get("limit"), field=f"{field}.limit", default=20
        )
        normalized["cli"] = _string(entry.get("cli"), field=f"{field}.cli")
    elif source_type == "tophub-daily":
        normalized["retries"] = _positive_int(
            entry.get("retries"), field=f"{field}.retries", default=3
        )
    elif source_type == "tophub-node-items":
        normalized["nodeid"] = nodeid
        normalized["date"] = _validate_date(
            _string(entry.get("date"), field=f"{field}.date"), field=f"{field}.date"
        )
        normalized["max_pages"] = _positive_int(
            entry.get("max_pages"), field=f"{field}.max_pages", default=20
        )
        normalized["delay"] = _non_negative_float(
            entry.get("delay"), field=f"{field}.delay", default=0.5
        )
        normalized["cookie"] = _string(entry.get("cookie"), field=f"{field}.cookie")
    elif source_type == "tophub-node-page":
        normalized["url"] = _validate_url(
            _string(entry.get("url"), field=f"{field}.url")
            or tophub_node_page.DEFAULT_URL,
            field=f"{field}.url",
        )
        normalized["limit"] = _non_negative_int(
            entry.get("limit"), field=f"{field}.limit", default=50
        )
        normalized["retries"] = _positive_int(
            entry.get("retries"), field=f"{field}.retries", default=3
        )
        normalized["nodeid"] = nodeid
        normalized["date"] = _validate_date(
            _string(entry.get("date"), field=f"{field}.date"), field=f"{field}.date"
        )

    return normalized


def load_sources_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Read ``sources.json`` and return the normalised configuration."""
    config_path = Path(path).expanduser() if path else DEFAULT_CONFIG
    try:
        with config_path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raise MaterialConfigError(f"config file not found: {config_path}") from None
    except OSError as exc:
        raise MaterialConfigError(
            f"cannot read config file {config_path}: {exc}"
        ) from None
    except json.JSONDecodeError as exc:
        raise MaterialConfigError(
            f"config file is not valid JSON: {config_path}: {exc}"
        ) from None

    if not isinstance(raw, dict):
        raise MaterialConfigError("config top level must be an object")

    cache_raw = raw.get("cache") or {}
    if not isinstance(cache_raw, dict):
        raise MaterialConfigError("cache must be an object")
    cache_dir = _string(cache_raw.get("dir"), field="cache.dir") or ".cache/material"
    max_age = _non_negative_float(
        cache_raw.get("max_age"), field="cache.max_age", default=DEFAULT_MAX_AGE
    )

    sources_raw = raw.get("sources") or []
    if not isinstance(sources_raw, list):
        raise MaterialConfigError("sources must be an array")
    sources = [
        _normalize_registry_entry(entry, index=index)
        for index, entry in enumerate(sources_raw)
    ]
    sources = [source for source in sources if source["enabled"]]

    return {
        "path": config_path,
        "cache": {"dir": cache_dir, "max_age": max_age},
        "sources": sources,
    }


def _resolve_cache_root(
    cache_dir: str | os.PathLike[str] | None, config: Mapping[str, Any]
) -> Path:
    if cache_dir is not None:
        root = Path(cache_dir).expanduser()
        return root if root.is_absolute() else (Path.cwd() / root)
    configured = str(config["cache"]["dir"])
    root = Path(configured).expanduser()
    if root.is_absolute():
        return root
    return Path(config["path"]).resolve().parent / root


# ---------------------------------------------------------------- source descriptions


def _registry_descriptors(
    config: Mapping[str, Any], source_types: set[str] | None
) -> list[dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    for entry in config["sources"]:
        if source_types and entry["type"] not in source_types:
            continue
        descriptors.append(
            {
                "kind": "registry",
                "type": entry["type"],
                "id": entry["id"],
                "name": entry["name"],
                "entry": entry,
            }
        )
    return descriptors


def _rss_descriptors(
    rss_config_path: str | os.PathLike[str] | None,
    feed_ids: Sequence[str] | None,
) -> tuple[list[dict[str, Any]], Path]:
    config_path = (
        Path(rss_config_path).expanduser() if rss_config_path else DEFAULT_RSS_CONFIG
    )
    try:
        config = rss.load_config(str(config_path))
        feeds = rss.select_feeds(config["feeds"], feed_ids)
    except rss.ConfigError as exc:
        raise MaterialConfigError(str(exc)) from exc

    descriptors = [
        {
            "kind": "rss",
            "type": "rss",
            "id": feed["id"],
            "name": feed["name"],
            "feed": feed,
        }
        for feed in feeds
    ]
    return descriptors, config_path


def _assign_cache_keys(descriptors: Sequence[dict[str, Any]]) -> None:
    bases = [_sanitize(f"{item['type']}_{item['name']}") for item in descriptors]
    counts = Counter(bases)
    used: set[str] = set()
    for descriptor, base in zip(descriptors, bases):
        key = base
        if counts[base] > 1:
            key = f"{base}_{_sanitize(descriptor['id'])}"
        original = key
        suffix = 2
        while key in used:
            key = f"{original}-{suffix}"
            suffix += 1
        used.add(key)
        descriptor["cache_key"] = key


def _rss_url_key(feed: Mapping[str, Any]) -> tuple[str, tuple[tuple[str, str], ...]]:
    headers = tuple(
        sorted(
            (str(key), str(value)) for key, value in (feed.get("headers") or {}).items()
        )
    )
    return feed["url"], headers


# ---------------------------------------------------------------- per-source fetching


def _fetch_tencent(entry: Mapping[str, Any]) -> dict[str, Any]:
    cli = tencent.resolve_cli(entry.get("cli"))
    text = tencent.run_hot(cli, entry["limit"], entry["timeout"])
    parsed = tencent.parse_hot(text)
    extra: dict[str, Any] = {}
    generated_at = parsed.get("generated_at")
    if generated_at:
        extra["list_generated_at"] = generated_at
    return make_document(
        feed="tencent-news-hot",
        generator="winnie_news.material/tencent.py",
        items=items_from_tencent_hot(parsed),
        extra=extra,
    )


def _fetch_tophub_daily(entry: Mapping[str, Any]) -> dict[str, Any]:
    sections = tophub_daily.fetch_news(
        timeout=entry["timeout"], retries=entry["retries"]
    )
    return make_document(
        feed="tophub-daily",
        generator="winnie_news.material/tophub_daily.py",
        items=items_from_tophub_daily(sections),
        extra={"sections": [section["title"] for section in sections]},
    )


def _fetch_tophub_node_items(entry: Mapping[str, Any]) -> dict[str, Any]:
    date = entry.get("date") or today()
    nodeid = str(entry["nodeid"])
    cookie = entry.get("cookie") or os.environ.get("TOPHUB_COOKIE", "")
    items = tophub_node_items.fetch_all(
        nodeid,
        date,
        max_pages=entry["max_pages"],
        delay=entry["delay"],
        cookie=cookie,
        timeout=entry["timeout"],
    )
    return make_document(
        feed="tophub-node-items",
        generator="winnie_news.material/tophub_node_items.py",
        items=items_from_node_items(items, date=date, nodeid=nodeid),
        extra={"nodeid": nodeid, "date": date},
    )


def _fetch_tophub_node_page(entry: Mapping[str, Any]) -> dict[str, Any]:
    html = tophub_node_page.fetch_html(
        entry["url"], timeout=entry["timeout"], retries=entry["retries"]
    )
    page_items = tophub_node_page.parse_items(html, limit=entry["limit"])
    if not page_items:
        raise MaterialFetchError(
            "no items parsed from the page; its HTML structure may have changed"
        )

    title_match = tophub_node_page.TITLE_RE.search(html)
    nodeid_match = tophub_node_page.NODEID_RE.search(html)
    page_title = strip_tags(title_match.group(1)) if title_match else ""
    nodeid = entry.get("nodeid") or (nodeid_match.group(1) if nodeid_match else "")
    source = page_title.split(" - ", 1)[0].strip() or "tophub.today"
    return make_document(
        feed="tophub-node-page",
        generator="winnie_news.material/tophub_node_page.py",
        feed_url=entry["url"],
        items=items_from_node_page(
            page_items, source=source, nodeid=str(nodeid) if nodeid else None
        ),
        extra={
            "nodeid": str(nodeid) if nodeid else None,
            "page_title": page_title,
            "date": entry.get("date") or today(),
        },
    )


_REGISTRY_FETCHERS = {
    "tencent": _fetch_tencent,
    "tophub-daily": _fetch_tophub_daily,
    "tophub-node-items": _fetch_tophub_node_items,
    "tophub-node-page": _fetch_tophub_node_page,
}


def _fetch_registry_document(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    fetcher = _REGISTRY_FETCHERS.get(descriptor["type"])
    if fetcher is None:  # pragma: no cover - validated while loading the config
        raise MaterialFetchError(f"unsupported source type: {descriptor['type']}")
    return fetcher(descriptor["entry"])


# ---------------------------------------------------------------- cached results


def _outcome_cached(
    document: Mapping[str, Any], path: Path, age: float
) -> dict[str, Any]:
    return {
        "document": document,
        "path": path,
        "cached": True,
        "age": age,
        "error": None,
    }


def _outcome_fetched(document: Mapping[str, Any], path: Path) -> dict[str, Any]:
    return {
        "document": document,
        "path": path,
        "cached": False,
        "age": 0.0,
        "error": None,
    }


def _outcome_error(error: str) -> dict[str, Any]:
    return {
        "document": None,
        "path": None,
        "cached": False,
        "age": 0.0,
        "error": error,
    }


def _resolve_registry_descriptor(
    descriptor: Mapping[str, Any],
    *,
    cache_root: Path,
    max_age: float,
    now: datetime,
    force: bool,
) -> dict[str, Any]:
    cache_key = descriptor["cache_key"]
    if not force:
        cached = _read_cache(
            cache_key, root=cache_root, max_age=max_age, now=now, force=False
        )
        if cached:
            return _outcome_cached(*cached)

    with _lock_for(cache_key):
        if not force:
            cached = _read_cache(
                cache_key, root=cache_root, max_age=max_age, now=now, force=False
            )
            if cached:
                return _outcome_cached(*cached)
        try:
            document = _fetch_registry_document(descriptor)
            path = _write_cache(cache_key, document, root=cache_root, now=now)
        except Exception as exc:
            logger.warning("%s fetch failed: %s", descriptor["name"], exc)
            return _outcome_error(f"{type(exc).__name__}: {exc}")
        return _outcome_fetched(document, path)


def _resolve_rss_descriptors(
    descriptors: Sequence[dict[str, Any]],
    *,
    cache_root: Path,
    max_age: float,
    now: datetime,
    force: bool,
    rss_config_path: Path,
    rss_limit: int | None,
    workers: int,
) -> dict[str, dict[str, Any]]:
    outcomes: dict[str, dict[str, Any]] = {}
    if not descriptors:
        return outcomes

    with _RSS_LOCK:
        pending: list[dict[str, Any]] = []
        for descriptor in descriptors:
            if not force:
                cached = _read_cache(
                    descriptor["cache_key"],
                    root=cache_root,
                    max_age=max_age,
                    now=now,
                )
                if cached:
                    outcomes[descriptor["cache_key"]] = _outcome_cached(*cached)
                    continue
            pending.append(descriptor)
        if not pending:
            return outcomes

        unique_feeds: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}
        for descriptor in pending:
            unique_feeds.setdefault(
                _rss_url_key(descriptor["feed"]), descriptor["feed"]
            )

        try:
            results = rss.collect(
                list(unique_feeds.values()),
                timeout=None,
                retries=None,
                limit=rss_limit,
                workers=workers,
            )
        except Exception as exc:
            logger.warning("RSS batch fetch failed: %s", exc)
            for descriptor in pending:
                outcomes[descriptor["cache_key"]] = _outcome_error(
                    f"{type(exc).__name__}: {exc}"
                )
            return outcomes

        by_url = {_rss_url_key(result["feed"]): result for result in results}
        for descriptor in pending:
            feed = descriptor["feed"]
            result = by_url.get(_rss_url_key(feed))
            if result is None:
                outcomes[descriptor["cache_key"]] = _outcome_error(
                    "no fetch result was returned"
                )
                continue
            if result.get("error"):
                outcomes[descriptor["cache_key"]] = _outcome_error(result["error"])
                continue

            parsed = result.get("parsed") or {}
            material = list(
                items_from_rss(
                    parsed.get("items") or [],
                    feed_id=feed["id"],
                    feed_name=feed["name"],
                    section=feed.get("section"),
                    tags=feed.get("tags"),
                    feed_title=parsed.get("title"),
                    feed_url=feed["url"],
                    language=feed.get("language") or parsed.get("language"),
                )
            )
            max_items = feed.get("max_items", 0) if rss_limit is None else rss_limit
            if max_items:
                material = material[:max_items]
            try:
                document, _ = rss.build_source_document(
                    feed,
                    items=material,
                    parsed=parsed,
                    config_path=str(rss_config_path),
                )
                path = _write_cache(
                    descriptor["cache_key"], document, root=cache_root, now=now
                )
            except Exception as exc:
                logger.warning("%s cache write failed: %s", feed["name"], exc)
                outcomes[descriptor["cache_key"]] = _outcome_error(
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            outcomes[descriptor["cache_key"]] = _outcome_fetched(document, path)

    return outcomes


# ---------------------------------------------------------------- public API


def get_all_materials(
    *,
    cache_dir: str | os.PathLike[str] | None = None,
    max_age: float | None = None,
    force: bool = False,
    source_types: Sequence[str] | None = None,
    config_path: str | os.PathLike[str] | None = None,
    rss_config_path: str | os.PathLike[str] | None = None,
    rss_feed_ids: Sequence[str] | None = None,
    rss_limit: int | None = None,
    workers: int = 5,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch the material documents for every source.

    Arguments:
      - ``cache_dir``: cache root; defaults to ``cache.dir`` from the config
        (relative to the config file's directory). A relative path passed to the
        function is resolved against the current working directory.
      - ``max_age``: cache lifetime in seconds; defaults to the config value
        (3600).
      - ``force``: ignore existing cache and fetch again (a new cache file is
        still written).
      - ``source_types``: restrict to these types, for example
        ``["rss", "tencent"]``.
      - ``rss_feed_ids``: restrict to these RSS feed ids (see
        ``rss_sources.json``).
      - ``rss_limit``: override every RSS feed's ``max_items``.
      - ``workers``: number of concurrent RSS fetches.
      - ``now``: current time for tests; defaults to the current time in
        Asia/Shanghai.

    Returns:
      {
        "generated_at": "...",
        "cache_dir": "...",
        "max_age": 3600,
        "count": 123,
        "documents": [material document, ...],
        "items": [every item, ...],  # extra gains source_type / source_name / source_id
        "sources": [
          {"type","id","name","count","cached","path","age"?,"error"?}
        ]
      }

    One source failing only records ``sources[].error`` and does not affect the
    others; a configuration problem raises ``MaterialConfigError``.
    """
    current = now or _now_shanghai()
    config = load_sources_config(config_path)
    cache_root = _resolve_cache_root(cache_dir, config)
    effective_max_age = (
        float(max_age) if max_age is not None else float(config["cache"]["max_age"])
    )
    if not math.isfinite(effective_max_age) or effective_max_age < 0:
        raise MaterialConfigError(
            f"max_age must be a finite non-negative number, got {max_age!r}"
        )
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise MaterialConfigError(
            f"workers must be a positive integer, got {workers!r}"
        )
    if rss_limit is not None and (
        isinstance(rss_limit, bool) or not isinstance(rss_limit, int) or rss_limit < 0
    ):
        raise MaterialConfigError(
            f"rss_limit must be a non-negative integer, got {rss_limit!r}"
        )

    type_filter: set[str] | None = None
    if source_types:
        type_filter = {
            _normalize_type(item, field="source_types", allow_rss=True)
            for item in source_types
        }

    descriptors = _registry_descriptors(config, type_filter)
    rss_descriptors: list[dict[str, Any]] = []
    rss_path = (
        Path(rss_config_path).expanduser() if rss_config_path else DEFAULT_RSS_CONFIG
    )
    if type_filter is None or "rss" in type_filter:
        rss_descriptors, rss_path = _rss_descriptors(rss_config_path, rss_feed_ids)
    descriptors.extend(rss_descriptors)
    _assign_cache_keys(descriptors)

    outcomes: dict[str, dict[str, Any]] = {}
    pending_registry: list[dict[str, Any]] = []
    for descriptor in descriptors:
        if descriptor["kind"] != "registry":
            continue
        cached = None
        if not force:
            cached = _read_cache(
                descriptor["cache_key"],
                root=cache_root,
                max_age=effective_max_age,
                now=current,
            )
        if cached:
            outcomes[descriptor["cache_key"]] = _outcome_cached(*cached)
        else:
            pending_registry.append(descriptor)

    if pending_registry:
        with ThreadPoolExecutor(
            max_workers=min(workers, len(pending_registry))
        ) as pool:
            futures = {
                pool.submit(
                    _resolve_registry_descriptor,
                    descriptor,
                    cache_root=cache_root,
                    max_age=effective_max_age,
                    now=current,
                    force=force,
                ): descriptor
                for descriptor in pending_registry
            }
            for future in as_completed(futures):
                descriptor = futures[future]
                try:
                    outcomes[descriptor["cache_key"]] = future.result()
                except Exception as exc:  # last resort; normally unreachable
                    logger.warning("%s processing failed: %s", descriptor["name"], exc)
                    outcomes[descriptor["cache_key"]] = _outcome_error(
                        f"{type(exc).__name__}: {exc}"
                    )

    pending_rss = [
        descriptor
        for descriptor in descriptors
        if descriptor["kind"] == "rss" and descriptor["cache_key"] not in outcomes
    ]
    outcomes.update(
        _resolve_rss_descriptors(
            pending_rss,
            cache_root=cache_root,
            max_age=effective_max_age,
            now=current,
            force=force,
            rss_config_path=rss_path,
            rss_limit=rss_limit,
            workers=workers,
        )
    )

    documents: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []

    for descriptor in descriptors:
        outcome = outcomes.get(descriptor["cache_key"]) or _outcome_error(
            "not processed"
        )
        document = outcome.get("document")
        status: dict[str, Any] = {
            "type": descriptor["type"],
            "id": descriptor["id"],
            "name": descriptor["name"],
            "count": document.get("count", 0) if document else 0,
            "cached": bool(outcome.get("cached")),
        }
        if outcome.get("path") is not None:
            status["path"] = str(outcome["path"])
        if outcome.get("cached"):
            status["age"] = round(float(outcome.get("age") or 0.0), 1)
        if outcome.get("error"):
            status["error"] = outcome["error"]
        sources.append(status)

        if not document:
            continue
        documents.append(document)
        for item in document.get("items") or []:
            copy = dict(item)
            extra = dict(item.get("extra") or {})
            extra["source_type"] = descriptor["type"]
            extra["source_name"] = descriptor["name"]
            extra["source_id"] = descriptor["id"]
            copy["extra"] = extra
            items.append(copy)

    return {
        "generated_at": now_iso(),
        "cache_dir": str(cache_root),
        "max_age": effective_max_age,
        "count": len(items),
        "documents": documents,
        "items": items,
        "sources": sources,
    }


# ---------------------------------------------------------------- CLI


def non_negative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite non-negative number")
    return number


def non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if number < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return number


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch news material from every source in one function/command "
            "(with a one-hour cache)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s\n"
            "  %(prog)s --force\n"
            "  %(prog)s --type rss --rss-feed chinanews-scroll\n"
            "  %(prog)s --type rss --type tencent --compact -o batch.json\n"
        ),
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"source registry config (default {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--rss-config",
        default=str(DEFAULT_RSS_CONFIG),
        help=f"RSS feed config (default {DEFAULT_RSS_CONFIG})",
    )
    parser.add_argument(
        "--type",
        action="append",
        dest="source_types",
        metavar="TYPE",
        help=(
            "only use the given source type, repeatable: rss / tencent / "
            "tophub-daily / tophub-node-items / tophub-node-page"
        ),
    )
    parser.add_argument(
        "--rss-feed",
        action="append",
        dest="rss_feed_ids",
        metavar="ID",
        help="only use the given RSS feed id, repeatable",
    )
    parser.add_argument(
        "--rss-limit",
        type=non_negative_int,
        default=None,
        help="override every RSS feed's max_items (0 = no limit)",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="cache root directory (defaults to cache.dir from the config)",
    )
    parser.add_argument(
        "--max-age",
        type=non_negative_float,
        default=None,
        help="cache lifetime in seconds (defaults to the config, 3600)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="ignore existing cache and fetch again (a new cache file is still written)",
    )
    parser.add_argument(
        "--workers",
        type=positive_int,
        default=5,
        help="number of concurrent RSS fetches (default 5)",
    )
    add_output_args(parser, formats=("json",))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        batch = get_all_materials(
            cache_dir=args.cache_dir,
            max_age=args.max_age,
            force=args.force,
            source_types=args.source_types,
            config_path=args.config,
            rss_config_path=args.rss_config,
            rss_feed_ids=args.rss_feed_ids,
            rss_limit=args.rss_limit,
            workers=args.workers,
        )
    except MaterialConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    with output_stream(args.out) as out:
        dump_json(batch, out, compact=args.compact)

    failed = [source for source in batch["sources"] if source.get("error")]
    if failed and not batch["documents"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
