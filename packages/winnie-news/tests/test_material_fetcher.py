"""Unit tests for the unified material interface and its cache rules."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from winnie_news.common.material import make_document, make_item
from winnie_news.material import fetcher


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _fake_document(title: str = "示例新闻") -> dict:
    return make_document(
        feed="tencent-news-hot",
        generator="test",
        items=[make_item(title=title, source="测试源")],
    )


class RegistryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.config = self.root / "material_sources.json"
        _write_json(
            self.config,
            {
                "cache": {"dir": "cache", "max_age": 3600},
                "sources": [
                    {
                        "type": "tencent",
                        "name": "测试热榜",
                        "id": "test-hot",
                        "limit": 5,
                    }
                ],
            },
        )

    def _run(self, **kwargs):
        params = {
            "config_path": self.config,
            "cache_dir": self.cache,
            "source_types": ["tencent"],
        }
        params.update(kwargs)
        return fetcher.get_all_materials(**params)

    def test_first_fetch_writes_cache_and_second_reads_it(self) -> None:
        with mock.patch.object(
            fetcher,
            "_fetch_registry_document",
            side_effect=lambda descriptor: _fake_document("第一条"),
        ) as fetch:
            first = self._run()
            second = self._run()

        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(first["sources"][0]["cached"])
        self.assertTrue(second["sources"][0]["cached"])
        self.assertEqual(first["count"], 1)
        self.assertEqual(second["count"], 1)
        self.assertEqual(second["items"][0]["title"], "第一条")
        self.assertEqual(second["items"][0]["extra"]["source_type"], "tencent")
        self.assertEqual(second["items"][0]["extra"]["source_name"], "测试热榜")

        path = Path(first["sources"][0]["path"])
        self.assertTrue(path.is_file())
        self.assertRegex(
            path.name,
            r"^tencent_测试热榜_\d{6}\.json$",
        )
        self.assertEqual(path.parent.parent.parent, self.cache)
        self.assertRegex(path.parent.parent.name, r"^\d{4}$")
        self.assertRegex(path.parent.name, r"^\d{4}$")
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["schema"],
            "winnie.news.material/v1",
        )

    def test_expired_cache_refetches(self) -> None:
        with mock.patch.object(
            fetcher,
            "_fetch_registry_document",
            side_effect=lambda descriptor: _fake_document(),
        ) as fetch:
            first = self._run()
            path = Path(first["sources"][0]["path"])
            old = time.time() - 7200
            os.utime(path, (old, old))
            second = self._run()

        self.assertEqual(fetch.call_count, 2)
        self.assertFalse(second["sources"][0]["cached"])
        self.assertNotEqual(second["sources"][0]["path"], str(path))

    def test_zero_max_age_always_fetches(self) -> None:
        with mock.patch.object(
            fetcher,
            "_fetch_registry_document",
            side_effect=lambda descriptor: _fake_document(),
        ) as fetch:
            self._run(max_age=0)
            self._run(max_age=0)

        self.assertEqual(fetch.call_count, 2)

    def test_corrupt_cache_falls_back_to_fetch(self) -> None:
        with mock.patch.object(
            fetcher,
            "_fetch_registry_document",
            side_effect=lambda descriptor: _fake_document(),
        ) as fetch:
            first = self._run()
            Path(first["sources"][0]["path"]).write_text("{not json", encoding="utf-8")
            second = self._run()

        self.assertEqual(fetch.call_count, 2)
        self.assertFalse(second["sources"][0]["cached"])

    def test_partial_failure_does_not_break_batch(self) -> None:
        _write_json(
            self.config,
            {
                "cache": {"dir": "cache", "max_age": 3600},
                "sources": [
                    {"type": "tencent", "name": "坏源", "id": "bad"},
                    {"type": "tophub-daily", "name": "好源", "id": "good"},
                ],
            },
        )

        def fake(descriptor):
            if descriptor["id"] == "bad":
                raise RuntimeError("simulated failure")
            return make_document(
                feed="tophub-daily",
                generator="test",
                items=[make_item(title="好新闻", source="测试源")],
            )

        with mock.patch.object(fetcher, "_fetch_registry_document", side_effect=fake):
            batch = self._run(source_types=["tencent", "tophub-daily"])

        self.assertEqual(batch["count"], 1)
        self.assertEqual(len(batch["documents"]), 1)
        statuses = {source["id"]: source for source in batch["sources"]}
        self.assertIn("error", statuses["bad"])
        self.assertNotIn("error", statuses["good"])

    def test_cache_from_yesterday_is_still_used(self) -> None:
        now = fetcher._now_shanghai()
        yesterday = now - timedelta(days=1)
        directory = self.cache / yesterday.strftime("%Y") / yesterday.strftime("%m%d")
        document = _fake_document()
        path = directory / "tencent_测试热榜_120000.json"
        _write_json(path, document)

        hit = fetcher._read_cache(
            "tencent_测试热榜",
            root=self.cache,
            max_age=3600,
            now=now,
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], path)


class RssCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.config = self.root / "material_sources.json"
        self.rss_config = self.root / "rss_sources.json"
        _write_json(
            self.config,
            {"cache": {"dir": "cache"}, "sources": []},
        )
        _write_json(
            self.rss_config,
            {
                "defaults": {"max_items": 2},
                "feeds": [
                    {
                        "id": "feed-a",
                        "name": "源A",
                        "url": "https://example.com/feed.xml",
                        "enabled": True,
                    },
                    {
                        "id": "feed-b",
                        "name": "源B",
                        "url": "https://example.com/feed.xml",
                        "enabled": True,
                    },
                ],
            },
        )
        self.calls: list[list[str]] = []

    def _fake_collect(self, feeds, *, timeout, retries, limit, workers):
        self.calls.append([feed["id"] for feed in feeds])
        feed = feeds[0]
        return [
            {
                "feed": feed,
                "parsed": {
                    "title": "测试 Feed",
                    "language": "zh-cn",
                    "items": [
                        {"title": "新闻一", "url": "https://example.com/1"},
                        {"title": "新闻二", "url": "https://example.com/2"},
                        {"title": "新闻三", "url": "https://example.com/3"},
                    ],
                },
                "error": None,
                "material": [],
            }
        ]

    def test_each_feed_gets_a_file_and_same_url_is_fetched_once(self) -> None:
        with mock.patch.object(
            fetcher.rss, "collect", side_effect=self._fake_collect
        ):
            first = fetcher.get_all_materials(
                config_path=self.config,
                rss_config_path=self.rss_config,
                source_types=["rss"],
                cache_dir=self.cache,
            )
            second = fetcher.get_all_materials(
                config_path=self.config,
                rss_config_path=self.rss_config,
                source_types=["rss"],
                cache_dir=self.cache,
            )

        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0], ["feed-a"])
        self.assertEqual(first["count"], 4)
        self.assertTrue(all(not source["cached"] for source in first["sources"]))
        self.assertTrue(all(source["cached"] for source in second["sources"]))

        names = sorted(Path(source["path"]).name for source in first["sources"])
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].startswith("rss_源A_"))
        self.assertTrue(names[1].startswith("rss_源B_"))
        for source in first["sources"]:
            self.assertRegex(
                Path(source["path"]).name,
                r"^rss_.+_\d{6}\.json$",
            )

    def test_rss_limit_override(self) -> None:
        with mock.patch.object(
            fetcher.rss, "collect", side_effect=self._fake_collect
        ):
            batch = fetcher.get_all_materials(
                config_path=self.config,
                rss_config_path=self.rss_config,
                source_types=["rss"],
                rss_limit=1,
                cache_dir=self.cache,
            )
        self.assertEqual(batch["count"], 2)
        self.assertEqual([source["count"] for source in batch["sources"]], [1, 1])

    def test_rss_partial_failure_is_isolated(self) -> None:
        def fake_collect(feeds, *, timeout, retries, limit, workers):
            feed = feeds[0]
            return [{"feed": feed, "error": "simulated timeout", "material": []}]

        with mock.patch.object(fetcher.rss, "collect", side_effect=fake_collect):
            batch = fetcher.get_all_materials(
                config_path=self.config,
                rss_config_path=self.rss_config,
                source_types=["rss"],
                cache_dir=self.cache,
            )
        self.assertEqual(batch["count"], 0)
        self.assertEqual(len(batch["sources"]), 2)
        self.assertTrue(all("error" in source for source in batch["sources"]))


class ConfigAndPathTests(unittest.TestCase):
    def test_unsupported_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            _write_json(
                path,
                {"sources": [{"type": "unknown", "name": "未知"}]},
            )
            with self.assertRaises(fetcher.MaterialConfigError):
                fetcher.load_sources_config(path)

    def test_cache_key_collision_gets_id_suffix(self) -> None:
        descriptors = [
            {"type": "rss", "id": "a", "name": "同名"},
            {"type": "rss", "id": "b", "name": "同名"},
        ]
        fetcher._assign_cache_keys(descriptors)
        self.assertEqual(
            [item["cache_key"] for item in descriptors],
            ["rss_同名_a", "rss_同名_b"],
        )

    def test_sanitize_removes_path_characters(self) -> None:
        self.assertEqual(fetcher._sanitize("a/b:c  d"), "a-b-c-d")
        self.assertNotRegex(fetcher._sanitize("../../etc/passwd"), r"[./]")


if __name__ == "__main__":
    unittest.main()
