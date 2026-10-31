"""Web search behind a small provider port.

The default is Bing's RSS output: no API key, one HTTP request, and a result
format that is explicitly meant for aggregation. A paid search API can be added
as another provider without touching the agent.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Protocol

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; Winnie/0.1; personal assistant)"

_TAG = re.compile(r"<[^>]+>")


class SearchError(RuntimeError):
    """The search provider could not answer."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int) -> list[SearchResult]: ...


class BingRSSSearch:
    name = "bing_rss"

    def __init__(
        self,
        endpoint: str = "https://cn.bing.com/search",
        *,
        timeout_seconds: float = 15.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.http = http

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        query = query.strip()
        if not query:
            return []
        client = self.http or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=10.0)
        )
        try:
            response = await client.get(
                self.endpoint,
                params={"q": query, "format": "rss", "count": str(limit)},
                headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml"},
                timeout=self.timeout_seconds,
                follow_redirects=True,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise SearchError(f"search request failed: {type(exc).__name__}") from exc
        finally:
            if self.http is None:
                await client.aclose()

        if response.status_code >= 400:
            raise SearchError(f"search returned HTTP {response.status_code}")
        return parse_bing_rss(response.text, limit)


class FakeSearchProvider:
    """Deterministic provider for tests and offline demos."""

    name = "fake"

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.results = results

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        if self.results is not None:
            return self.results[:limit]
        return [
            SearchResult(
                title=f"Fake result for {query}",
                url="https://example.com/fake",
                snippet=f"Deterministic fake search result for {query}.",
            )
        ][:limit]


def parse_bing_rss(text: str, limit: int) -> list[SearchResult]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise SearchError("search returned a body that is not RSS") from exc
    results: list[SearchResult] = []
    for item in root.iter("item"):
        title = _plain(item.findtext("title") or "")
        url = (item.findtext("link") or "").strip()
        snippet = _plain(item.findtext("description") or "")
        if not title or not url:
            continue
        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=snippet[:400],
            )
        )
        if len(results) >= limit:
            break
    if not results:
        raise SearchError("search returned no results")
    return results


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", value))).strip()
