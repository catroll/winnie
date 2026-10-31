"""Unit tests for ``material/rss.py`` (stdlib unittest, no third-party needs).

Run from the package root:

    python -m pytest tests/test_rss.py -q
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from winnie_news.common.http import FetchError
from winnie_news.common.material import items_from_rss
from winnie_news.common.timeutil import normalize_time
from winnie_news.material import rss

RSS_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>示例频道</title>
    <link>https://example.com/</link>
    <language>zh-cn</language>
    <item>
      <title><![CDATA[标题 <b>一</b>]]></title>
      <link>https://example.com/a</link>
      <description><![CDATA[<p>摘要 &amp; 更多</p>]]></description>
      <pubDate>Wed, 21 Oct 2026 00:14:12 +0800</pubDate>
      <guid isPermaLink="false">guid-1</guid>
      <category>科技</category>
      <source url="https://example.com">新华社</source>
    </item>
    <item>
      <title>第二条</title>
      <link>https://example.com/b</link>
      <description>没有 guid 的条目</description>
    </item>
  </channel>
</rss>
"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en">
  <title>Atom Feed</title>
  <link rel="alternate" href="https://example.com/"/>
  <entry>
    <title>Hello</title>
    <link rel="alternate" href="https://example.com/atom-a"/>
    <id>tag:example.com,2026:a</id>
    <updated>2026-10-20T12:00:00Z</updated>
    <summary type="html">&lt;p&gt;World&lt;/p&gt;</summary>
    <author><name>Alice</name></author>
    <category term="news"/>
  </entry>
</feed>
"""

RDF_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns="http://purl.org/rss/1.0/">
  <channel><title>RDF Feed</title><link>https://example.com/</link></channel>
  <item rdf:about="https://example.com/rdf-a">
    <title>RDF 条目</title>
    <link>https://example.com/rdf-a</link>
    <description>RDF 摘要</description>
  </item>
</rdf:RDF>
"""


class ParseFeedTest(unittest.TestCase):
    def test_rss(self):
        parsed = rss.parse_feed_xml(RSS_SAMPLE.encode("utf-8"))
        self.assertEqual(parsed["title"], "示例频道")
        self.assertEqual(parsed["link"], "https://example.com/")
        self.assertEqual(parsed["language"], "zh-cn")
        self.assertEqual(len(parsed["items"]), 2)

        first = parsed["items"][0]
        self.assertEqual(first["id"], "guid-1")
        self.assertEqual(first["source"], "新华社")
        self.assertEqual(first["url"], "https://example.com/a")
        self.assertEqual(first["author"], None)
        self.assertEqual(first["tags"], ["科技"])

        material = list(
            items_from_rss(
                parsed["items"],
                feed_id="demo",
                feed_name="示例源",
                feed_title=parsed["title"],
                feed_url="https://example.com/rss",
            )
        )
        self.assertEqual(material[0]["title"], "标题 一")
        self.assertEqual(material[0]["content"], "摘要 & 更多")
        self.assertEqual(material[0]["source"], "新华社")
        self.assertEqual(material[0]["published_at"], "2026-10-21T00:14:12+08:00")
        self.assertEqual(material[1]["source"], "示例源")
        self.assertIsNone(material[1]["published_at"])

    def test_atom(self):
        parsed = rss.parse_feed_xml(ATOM_SAMPLE.encode("utf-8"))
        self.assertEqual(parsed["title"], "Atom Feed")
        self.assertEqual(parsed["link"], "https://example.com/")
        self.assertEqual(parsed["language"], "en")
        first = parsed["items"][0]
        self.assertEqual(first["id"], "tag:example.com,2026:a")
        self.assertEqual(first["url"], "https://example.com/atom-a")
        self.assertEqual(first["author"], "Alice")
        self.assertEqual(first["tags"], ["news"])

        material = list(
            items_from_rss(
                parsed["items"],
                feed_id="atom",
                feed_name="Atom 示例",
                feed_title=parsed["title"],
                language=parsed["language"],
            )
        )
        self.assertEqual(material[0]["content"], "World")
        self.assertEqual(material[0]["published_at"], "2026-10-20T20:00:00+08:00")
        self.assertEqual(material[0]["extra"]["language"], "en")

    def test_rdf(self):
        parsed = rss.parse_feed_xml(RDF_SAMPLE.encode("utf-8"))
        self.assertEqual(parsed["title"], "RDF Feed")
        self.assertEqual(len(parsed["items"]), 1)
        self.assertEqual(parsed["items"][0]["id"], "https://example.com/rdf-a")
        self.assertEqual(parsed["items"][0]["content"], "RDF 摘要")

    def test_repairs_undeclared_html_entities(self):
        raw = (
            "<rss version='2.0'><channel><title>T</title><item>"
            "<title>A&nbsp;B</title><description>1 & 2</description>"
            "</item></channel></rss>"
        ).encode("utf-8")
        parsed = rss.parse_feed_xml(raw)
        self.assertEqual(parsed["items"][0]["title"].replace("\xa0", " "), "A B")
        self.assertEqual(parsed["items"][0]["content"], "1 & 2")

    def test_unknown_root_is_error(self):
        with self.assertRaises(ValueError):
            rss.parse_feed_xml(b"<html><body>not a feed</body></html>")

    def test_empty_feed(self):
        parsed = rss.parse_feed_xml(
            b"<rss version='2.0'><channel><title>Empty</title></channel></rss>"
        )
        self.assertEqual(parsed["items"], [])


class ConfigTest(unittest.TestCase):
    def _write_config(self, payload):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "rss.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        self.addCleanup(lambda: (os.remove(path), os.rmdir(directory)))
        return path

    def test_load_and_expand_google_keywords(self):
        path = self._write_config(
            {
                "defaults": {"timeout": 9, "retries": 1},
                "feeds": [
                    {
                        "id": "a",
                        "name": "A",
                        "url": "https://example.com/a.xml",
                        "section": "要闻",
                        "tags": ["中文"],
                    },
                    {
                        "name": "B",
                        "url": "https://example.com/b.xml",
                        "enabled": False,
                    },
                ],
                "google_news": {
                    "url_template": "https://news.google.com/rss/search?q={query}",
                    "hl": "zh-CN",
                    "keywords": [
                        {
                            "name": "人工智能",
                            "query": 'AI OR "artificial intelligence"',
                        },
                        {"query": "OpenAI", "enabled": False},
                    ],
                },
            }
        )
        config = rss.load_config(path)
        self.assertEqual(config["defaults"]["timeout"], 9.0)
        self.assertEqual(len(config["feeds"]), 3)
        self.assertEqual(config["feeds"][1]["id"], "b")

        google = config["feeds"][2]
        self.assertEqual(google["name"], "Google 新闻：人工智能")
        self.assertIsNone(google["section"])
        self.assertIn("q=AI+OR+%22artificial+intelligence%22", google["url"])
        self.assertIn("hl=zh-CN", google["url"])
        self.assertIn("人工智能", google["tags"])

        selected = rss.select_feeds(config["feeds"], None)
        self.assertEqual([feed["id"] for feed in selected], ["a", google["id"]])

    def test_top_level_array(self):
        path = self._write_config(
            [{"id": "only", "url": "https://example.com/rss.xml"}]
        )
        config = rss.load_config(path)
        self.assertEqual(config["feeds"][0]["id"], "only")
        self.assertEqual(config["feeds"][0]["name"], "only")

    def test_feed_can_be_plain_url(self):
        path = self._write_config(
            {"feeds": ["https://www.chinanews.com.cn/rss/china.xml"]}
        )
        config = rss.load_config(path)
        feed = config["feeds"][0]
        self.assertEqual(feed["id"], "www-chinanews-com-cn")
        self.assertEqual(feed["name"], "www-chinanews-com-cn")
        self.assertEqual(feed["url"], "https://www.chinanews.com.cn/rss/china.xml")

    def test_missing_url(self):
        path = self._write_config({"feeds": [{"name": "no url"}]})
        with self.assertRaises(rss.ConfigError):
            rss.load_config(path)

    def test_unknown_feed_id(self):
        with self.assertRaises(rss.ConfigError):
            rss.select_feeds([{"id": "a", "enabled": True}], ["nope"])

    def test_bad_json(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.addCleanup(lambda: (os.remove(path), os.rmdir(directory)))
        with self.assertRaises(rss.ConfigError):
            rss.load_config(path)


class CollectTest(unittest.TestCase):
    def test_partial_failure_keeps_order(self):
        feeds = [
            {"id": "one", "name": "One", "url": "https://example.com/1"},
            {"id": "bad", "name": "Bad", "url": "https://example.com/bad"},
            {"id": "two", "name": "Two", "url": "https://example.com/2"},
        ]

        def fake_fetch(feed, *, timeout, retries):
            if feed["id"] == "bad":
                raise FetchError("boom")
            return {
                "title": feed["name"],
                "language": None,
                "items": [
                    {
                        "title": f"{feed['id']}-title",
                        "content": "",
                        "url": f"https://example.com/{feed['id']}",
                        "published_at": None,
                        "id": None,
                        "source": "",
                        "author": None,
                        "tags": [],
                    }
                ],
            }

        with mock.patch.object(rss, "fetch_feed", side_effect=fake_fetch):
            with mock.patch.object(rss, "logger"):
                results = rss.collect(
                    feeds, timeout=None, retries=None, limit=None, workers=3
                )

        self.assertEqual(
            [result["feed"]["id"] for result in results], ["one", "bad", "two"]
        )
        self.assertEqual(results[0]["material"][0]["title"], "one-title")
        self.assertEqual(results[1]["error"], "boom")
        self.assertEqual(results[1]["material"], [])
        self.assertEqual(results[2]["material"][0]["title"], "two-title")


class NormalizeTimeRssTest(unittest.TestCase):
    def test_rfc2822(self):
        self.assertEqual(
            normalize_time("Wed, 21 Oct 2026 00:14:12 +0800"),
            "2026-10-21T00:14:12+08:00",
        )


if __name__ == "__main__":
    unittest.main()
