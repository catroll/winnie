"""The search stage: find reports the material collection did not contain.

Search results are untrusted input. A snippet is not the article, and a result
without a URL is not attributable, so this stage keeps the URL, the query that
found it and the snippet, and nothing else. The writing stage is told that these
are leads, not facts.

Deduplication is against the event's own article URLs, so the stage adds new
coverage instead of repeating what the event already has.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from winnie_news.aggregate.models import Event
from winnie_news.common.timeutil import now_iso
from winnie_news.ports import SearchPort

from .models import EventSearch, SearchBatch, SearchHit
from .queries import build_queries

logger = logging.getLogger(__name__)


class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries_per_event: int = 3
    results_per_query: int = 5
    concurrency: int = 3
    # A snippet shorter than this cannot support a sentence in the draft.
    min_snippet_chars: int = 20


# Domains whose results are aggregators of other articles; they add a lead but
# rarely the original report. Kept as a marker, not a block: the user may still
# want them.
_AGGREGATOR_HOSTS = (
    "baidu.com",
    "bing.com",
    "google.com",
    "so.com",
    "sogou.com",
    "toutiao.com",
)

_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)


async def search_events(
    events: list[Event],
    provider: SearchPort | None,
    *,
    config: SearchConfig | None = None,
) -> SearchBatch:
    """Search for each event's missing coverage.

    ``provider=None`` is a supported state: the pipeline continues without
    supplementary reports and records why, rather than failing a run because a
    search backend is not configured.
    """
    cfg = config or SearchConfig()
    if provider is None:
        return SearchBatch(
            generated_at=now_iso(),
            events=[EventSearch(event_id=event.id) for event in events],
            warnings=["search is disabled: no provider is configured"],
        )

    semaphore = asyncio.Semaphore(max(1, cfg.concurrency))

    async def one(event: Event) -> EventSearch:
        async with semaphore:
            return await _search_event(event, provider, cfg)

    results = await asyncio.gather(*(one(event) for event in events))
    batch = SearchBatch(generated_at=now_iso(), events=list(results))
    batch.count = sum(len(result.hits) for result in results)
    logger.info(
        "search: %d events, %d hits (%d new) using %s",
        len(events),
        batch.count,
        sum(result.new for result in results),
        provider.name,
    )
    return batch


async def _search_event(
    event: Event, provider: SearchPort, config: SearchConfig
) -> EventSearch:
    queries = build_queries(event, limit=config.queries_per_event)
    result = EventSearch(event_id=event.id, queries=queries)
    known_urls = {_canonical(article.url or "") for article in event.articles}
    seen: set[str] = set()

    for query in queries:
        try:
            found = await provider.search(query, config.results_per_query)
        except Exception as exc:  # noqa: BLE001 - one query must not kill the batch
            message = f"{query!r}: {type(exc).__name__}: {exc}"
            logger.warning("search failed for %s", message)
            result.errors.append(message)
            continue
        for raw in found:
            url = str(getattr(raw, "url", "") or "").strip()
            title = str(getattr(raw, "title", "") or "").strip()
            if not url or not title or not _HTTP_RE.match(url):
                continue
            key = _canonical(url)
            if key in seen:
                continue
            seen.add(key)
            snippet = " ".join(str(getattr(raw, "snippet", "") or "").split())
            hit = SearchHit(
                title=title,
                url=url,
                snippet=snippet,
                source=_source_of(url),
                query=query,
                status=_status(url, snippet, known_urls, config),
            )
            result.hits.append(hit)
            if hit.status == "known":
                result.known += 1
            elif hit.status == "suspect":
                result.suspect += 1
            else:
                result.new += 1
    return result


def _status(
    url: str, snippet: str, known_urls: set[str], config: SearchConfig
) -> str:
    if _canonical(url) in known_urls:
        return "known"
    if len(snippet) < config.min_snippet_chars:
        return "suspect"
    return "new"


def _canonical(url: str) -> str:
    text = url.strip().lower().split("#", 1)[0].split("?", 1)[0]
    return text.rstrip("/")


def _source_of(url: str) -> str | None:
    host = urlparse(url).hostname or ""
    host = host.removeprefix("www.")
    if not host:
        return None
    if any(host.endswith(domain) for domain in _AGGREGATOR_HOSTS):
        return f"aggregator:{host}"
    return host


def usable_hits(result: EventSearch) -> list[SearchHit]:
    """Hits the writing stage may cite.

    ``known`` results repeat an article the event already holds and ``suspect``
    results have no usable snippet: including either would duplicate sources or
    invite a sentence nothing supports.
    """
    return [hit for hit in result.hits if hit.status == "new"]
