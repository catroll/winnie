"""The news search provider, taken from the assistant's configuration.

Search is one external dependency, not two: the assistant already chooses a
provider, and the news stage reuses that choice. With ``provider: fake`` the
stage runs offline, which is what the tests and a demo use.
"""

from __future__ import annotations

import httpx

from winnie_assistant.search import BingRSSSearch, FakeSearchProvider, SearchProvider

from winnie_api.settings import Settings


def build_news_search(
    settings: Settings, http: httpx.AsyncClient | None
) -> SearchProvider:
    config = settings.assistant.search
    if config.provider == "fake":
        return FakeSearchProvider()
    return BingRSSSearch(
        config.endpoint,
        timeout_seconds=config.timeout_seconds,
        http=http,
    )
