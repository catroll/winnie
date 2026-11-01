"""End-to-end pipeline tests against the deterministic fake model."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from winnie_news.common.timeutil import now as news_now
from winnie_news.config import NewsConfig
from winnie_news.fake import FakeNewsLLM
from winnie_news.material.fetcher import get_all_materials
from winnie_news.service import NewsService, RunBusy
from winnie_news.store import NewsStore

# The digest keeps the last two calendar days, so the fixtures have to be recent
# for the batch to produce one. The prose stays dateless for that reason.
_FIXTURE_DAY = news_now().date() - timedelta(days=1)


def _at(hour: int) -> str:
    return f"{_FIXTURE_DAY.isoformat()}T{hour:02d}:00:00+08:00"


FIXTURES = [
    {
        "title": "四川雅安发生6.2级地震 已致12人受伤",
        "content": (
            "当天凌晨3时12分，四川雅安市芦山县发生6.2级地震，震源深度10公里，"
            "已造成12人受伤。当地已启动应急响应。"
        ),
        "source": "中新网",
        "url": "https://example.com/a1",
        "published_at": _at(5),
        "rank": 1,
    },
    {
        "title": "雅安芦山6.2级地震：救援力量赶赴震中",
        "content": (
            "当天凌晨，四川雅安芦山县发生6.2级地震。消防救援队伍已赶赴震中，"
            "目前报告12人受伤，部分房屋受损。"
        ),
        "source": "新华社",
        "url": "https://example.com/a2",
        "published_at": _at(6),
        "rank": 2,
    },
    {
        "title": "某公司发布第三季度财报 营收增长15%",
        "content": (
            "某科技公司今日发布第三季度财报，营收同比增长15%，净利润达到3.2亿元。"
            "公司称增长主要来自云业务。"
        ),
        "source": "财经网",
        "url": "https://example.com/b1",
        "published_at": _at(9),
        "rank": 3,
    },
    {
        "title": "点击下载APP，查看更多精彩内容",
        "content": "",
        "source": "广告",
        "url": "https://example.com/ad",
        "published_at": _at(9),
    },
]


def material_batch(items=FIXTURES) -> dict:
    from winnie_news.common.material import make_document, make_item

    documents = [
        make_document(
            feed="rss",
            generator="test",
            items=[make_item(**item) for item in items],
        )
    ]
    return {
        "generated_at": "2026-10-21T10:00:00+08:00",
        "cache_dir": "/tmp/cache",
        "max_age": 3600,
        "count": len(items),
        "documents": documents,
        "items": [item for document in documents for item in document["items"]],
        "sources": [],
    }


def build_service(tmp_path, *, keep_runs: int = 50) -> NewsService:
    config = NewsConfig(enabled=True, root=str(tmp_path), keep_runs=keep_runs)
    return NewsService(
        config,
        llm=FakeNewsLLM(),
        material_source=lambda **kwargs: material_batch(),
    )


@pytest.mark.asyncio
async def test_pipeline_produces_drafts_reviews_and_a_digest(tmp_path) -> None:
    service = build_service(tmp_path)
    await service.startup()
    run = await service.run_now()

    assert run.status == "done"
    assert {stage.name for stage in run.stages} == {
        "material",
        "clean",
        "aggregate",
        "search",
        "generate",
        "review",
        "digest",
    }
    assert all(stage.status in ("ok", "skipped") for stage in run.stages)
    assert run.stats["materials"] == 4
    assert run.stats["cleaned"] == 3  # the advert is dropped by rules
    assert run.stats["drafts"] == run.stats["events"]

    drafts = service.run_drafts(run.id)
    assert drafts
    for entry in drafts:
        markdown = service.draft_markdown(run.id, entry["event_id"])
        assert markdown.startswith("# ")
        assert "tags: " in markdown
        assert "example.com" not in markdown
        text = service.draft_text(run.id, entry["event_id"])
        assert text.startswith("title\n")

    digest, fmt, text = service.digest(run.id)
    assert fmt == "standard"
    assert digest.count == run.stats["drafts"]
    assert run.stats["digest_items"] == digest.count
    assert "以下为" in text
    assert "### 一、国内要闻" in text
    assert (tmp_path / "runs" / run.id / "digest.json").is_file()
    assert (tmp_path / "runs" / run.id / "digest.md").is_file()

    brief = service.digest(run.id, fmt="brief")[2]
    assert brief.startswith("📰 ")
    assert "【国内】" in brief
    table = service.digest(run.id, fmt="table")[2]
    assert "| 序号 | 日期 | 分类 | 新闻标题 | 核心要点 |" in table

    saved = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    assert saved["schema"] == "winnie.news.event/v1"
    assert saved["count"] == len(service.list_events())

    store = NewsStore(tmp_path)
    assert store.load_run(run.id).status == "done"


@pytest.mark.asyncio
async def test_a_second_run_does_not_attach_the_same_articles(tmp_path) -> None:
    service = build_service(tmp_path)
    await service.startup()
    first = await service.run_now()
    events_after_first = len(service.list_events())

    second = await service.run_now()
    assert len(service.list_events()) == events_after_first
    aggregate = next(stage for stage in second.stages if stage.name == "aggregate")
    assert aggregate.count == 0
    assert second.stats["drafts"] == 0
    assert second.stats["digest_items"] == 0
    assert first.id != second.id


@pytest.mark.asyncio
async def test_old_runs_are_pruned(tmp_path) -> None:
    service = build_service(tmp_path, keep_runs=1)
    await service.startup()
    first = await service.run_now()
    second = await service.run_now()
    remaining = {record.id for record in service.list_runs(limit=10)}
    assert second.id in remaining
    assert first.id not in remaining


@pytest.mark.asyncio
async def test_start_run_refuses_a_concurrent_run(tmp_path) -> None:
    service = build_service(tmp_path)
    await service.startup()
    run = await service.start_run()
    with pytest.raises(RunBusy):
        await service.start_run()
    await service._task
    assert service.get_run(run.id).status == "done"


def test_the_real_material_source_is_the_default() -> None:
    config = NewsConfig()
    service = NewsService(config, llm=FakeNewsLLM())
    assert service._material_source is get_all_materials
