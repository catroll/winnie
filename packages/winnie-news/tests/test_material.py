"""Unit tests for ``common/material.py`` (stdlib unittest, no third-party needs).

Run from the package root:

    python -m pytest tests/test_material.py -q
"""

import unittest

from winnie_news import MATERIAL_SCHEMA
from winnie_news.common.material import (
    ITEM_FIELDS,
    dedup_key,
    items_from_node_items,
    items_from_node_page,
    items_from_rss,
    items_from_tencent_hot,
    items_from_tophub_daily,
    make_document,
    make_item,
    validate_document,
)
from winnie_news.common.timeutil import normalize_time


class NormalizeTimeTest(unittest.TestCase):
    def test_minute_precision(self):
        self.assertEqual(
            normalize_time("2026-10-20 20:11"), "2026-10-20T20:11:00+08:00"
        )

    def test_time_only_needs_date(self):
        self.assertEqual(
            normalize_time("23:40", date="2026-10-20"),
            "2026-10-20T23:40:00+08:00",
        )
        self.assertIsNone(normalize_time("23:40"))

    def test_date_only_is_kept(self):
        self.assertEqual(normalize_time("2026/10/20"), "2026-10-20")

    def test_keeps_explicit_offset(self):
        self.assertEqual(
            normalize_time("2026-10-20T20:11:00+08:00"),
            "2026-10-20T20:11:00+08:00",
        )

    def test_rfc2822_is_converted_to_shanghai(self):
        self.assertEqual(
            normalize_time("Wed, 21 Oct 2026 00:14:12 +0000"),
            "2026-10-21T08:14:12+08:00",
        )

    def test_iso_z_suffix(self):
        self.assertEqual(
            normalize_time("2026-10-20T12:00:00Z"),
            "2026-10-20T20:00:00+08:00",
        )

    def test_invalid_returns_none(self):
        self.assertIsNone(normalize_time("昨天晚上"))


class MakeItemTest(unittest.TestCase):
    def test_fixed_keys_and_defaults(self):
        item = make_item(title=" 标题 ", content=" 正文 ", source="新华网")
        self.assertEqual(tuple(item), ITEM_FIELDS)
        self.assertEqual(item["title"], "标题")
        self.assertEqual(item["content"], "正文")
        self.assertIsNone(item["id"])
        self.assertIsNone(item["published_at"])
        self.assertIsNone(item["rank"])
        self.assertEqual(item["tags"], [])
        self.assertEqual(item["extra"], {})

    def test_cleans_html_and_whitespace(self):
        item = make_item(title="<b>标题</b>&amp;", content=" 多   空白 ")
        self.assertEqual(item["title"], "标题&")
        self.assertEqual(item["content"], "多 空白")


class ConverterTest(unittest.TestCase):
    def test_tophub_daily(self):
        sections = [
            {
                "title": "晚报",
                "items": [
                    {"title": "法国骚乱再升级", "summary": "多地宵禁。"},
                    {"title": ""},
                ],
            }
        ]
        items = list(items_from_tophub_daily(sections))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["section"], "晚报")
        self.assertEqual(items[0]["source"], "tophub.today")
        self.assertEqual(items[0]["content"], "多地宵禁。")
        self.assertIsNone(items[0]["published_at"])

    def test_node_items(self):
        raw = [
            {
                "ID": 251098427,
                "title": "问题标题",
                "description": "问题描述",
                "url": "https://www.zhihu.com/question/1",
                "sitename": "知乎",
                "time": "23:40",
                "extra": "70 万热度",
                "domain": "zhihu.com",
                "md5": "abc",
                "thumbnail": "",
            }
        ]
        item = list(items_from_node_items(raw, date="2026-10-20", nodeid="6"))[0]
        self.assertEqual(item["id"], "251098427")
        self.assertEqual(item["source"], "知乎")
        self.assertEqual(item["published_at"], "2026-10-20T23:40:00+08:00")
        self.assertEqual(item["extra"]["heat"], "70 万热度")
        self.assertEqual(item["extra"]["nodeid"], "6")
        self.assertNotIn("thumbnail", item["extra"])

    def test_node_page(self):
        raw = [
            {
                "rank": 1,
                "id": "251098427",
                "title": "问题标题",
                "url": "https://www.zhihu.com/question/1",
                "heat": "70 万热度",
            }
        ]
        item = list(items_from_node_page(raw, source="知乎热榜", nodeid="6"))[0]
        self.assertEqual(item["rank"], 1)
        self.assertEqual(item["source"], "知乎热榜")
        self.assertEqual(item["content"], "")
        self.assertEqual(item["extra"]["heat"], "70 万热度")

    def test_tencent_hot(self):
        document = {
            "items": [
                {
                    "rank": 1,
                    "title": "标题",
                    "summary": "摘要",
                    "media": "新华网",
                    "published_at": "2026-10-20 20:11",
                    "url": "https://view.inews.qq.com/a/1",
                }
            ]
        }
        item = list(items_from_tencent_hot(document))[0]
        self.assertEqual(item["source"], "新华网")
        self.assertEqual(item["published_at"], "2026-10-20T20:11:00+08:00")
        self.assertEqual(item["rank"], 1)

    def test_rss(self):
        raw = [
            {
                "id": "guid-1",
                "title": "<b>标题</b> &amp; 更多",
                "content": "<p>摘要</p>",
                "source": "",
                "url": "https://example.com/a",
                "published_at": "Wed, 21 Oct 2026 00:14:12 +0800",
                "author": "记者",
                "tags": ["科技", "科技"],
            },
            {"title": ""},
        ]
        items = list(
            items_from_rss(
                raw,
                feed_id="demo",
                feed_name="示例源",
                section="要闻",
                tags=["中文"],
                feed_title="示例频道",
                feed_url="https://example.com/rss",
                language="zh-cn",
            )
        )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], "guid-1")
        self.assertEqual(item["title"], "标题 & 更多")
        self.assertEqual(item["content"], "摘要")
        self.assertEqual(item["source"], "示例源")
        self.assertEqual(item["section"], "要闻")
        self.assertEqual(item["tags"], ["中文", "科技"])
        self.assertEqual(item["published_at"], "2026-10-21T00:14:12+08:00")
        self.assertEqual(item["extra"]["feed_id"], "demo")
        self.assertEqual(item["extra"]["author"], "记者")
        self.assertEqual(item["extra"]["categories"], ["科技"])


class DocumentTest(unittest.TestCase):
    def test_document_is_valid(self):
        items = [make_item(title="标题", source="新华网")]
        doc = make_document(
            feed="tencent-news-hot",
            generator="news/material/tencent_news_hot.py",
            items=items,
            extra={"list_generated_at": "2026-10-20T23:46:00+08:00"},
        )
        self.assertEqual(doc["schema"], MATERIAL_SCHEMA)
        self.assertEqual(doc["count"], 1)
        self.assertEqual(doc["feed"]["id"], "tencent-news-hot")
        self.assertEqual(validate_document(doc), [])

    def test_validation_catches_problems(self):
        doc = make_document(
            feed="tophub-daily",
            generator="news/material/tophub_daily.py",
            items=[],
        )
        doc["count"] = 3
        doc["items"].append({"title": ""})
        problems = validate_document(doc)
        self.assertTrue(any("count" in problem for problem in problems))
        self.assertTrue(any("missing fields" in problem for problem in problems))
        self.assertTrue(any("title" in problem for problem in problems))

    def test_unknown_feed(self):
        with self.assertRaises(KeyError):
            make_document(feed="nope", generator="x", items=[])


class DedupKeyTest(unittest.TestCase):
    def test_priority(self):
        self.assertEqual(
            dedup_key({"id": "1", "source": "知乎", "url": "u"}), "id:知乎:1"
        )
        self.assertEqual(dedup_key({"url": "u"}), "url:u")
        self.assertTrue(
            dedup_key({"title": "t", "source": "s", "published_at": "p"}).startswith(
                "hash:"
            )
        )


if __name__ == "__main__":
    unittest.main()
