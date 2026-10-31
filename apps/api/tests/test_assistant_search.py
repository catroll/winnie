from __future__ import annotations

import pytest

from winnie_assistant.search import SearchError, parse_bing_rss

FEED = """\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>必应：python asyncio</title>
    <item>
      <title>Python &amp; asyncio</title>
      <link>https://example.com/asyncio</link>
      <description>&lt;b&gt;Async&lt;/b&gt; I/O &amp; more</description>
      <pubDate>Fri, 30 Oct 2026 19:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Second result</title>
      <link>https://example.com/second</link>
      <description>Plain snippet</description>
    </item>
  </channel>
</rss>
"""


def test_bing_rss_is_parsed_and_cleaned() -> None:
    results = parse_bing_rss(FEED, 5)

    assert [result.title for result in results] == [
        "Python & asyncio",
        "Second result",
    ]
    assert results[0].url == "https://example.com/asyncio"
    assert results[0].snippet == "Async I/O & more"


def test_limit_is_respected() -> None:
    assert len(parse_bing_rss(FEED, 1)) == 1


def test_non_rss_body_is_an_error() -> None:
    with pytest.raises(SearchError, match="not RSS"):
        parse_bing_rss("<html><body>blocked", 5)


def test_empty_feed_is_an_error() -> None:
    with pytest.raises(SearchError, match="no results"):
        parse_bing_rss(
            '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>', 5
        )
