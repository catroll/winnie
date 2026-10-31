"""Service operations: abandoned runs, source health and draft regeneration."""

from __future__ import annotations

import pytest

from winnie_news.common.material import make_document, make_item
from winnie_news.common.timeutil import now_iso
from winnie_news.config import NewsConfig
from winnie_news.fake import FakeNewsLLM
from winnie_news.service import NewsService, RunBusy
from winnie_news.store import NewsStore, RunRecord, StageRun, new_run_id

ITEMS = [
    {
        "title": "四川雅安发生6.2级地震 已致12人受伤",
        "content": (
            "10月21日凌晨3时12分，四川雅安市芦山县发生6.2级地震，震源深度10公里，"
            "已造成12人受伤，部分房屋受损。当地已启动应急响应。"
        ),
        "source": "中新网",
        "url": "https://example.com/a1",
        "published_at": "2026-10-21T05:00:00+08:00",
        "rank": 1,
    },
    {
        "title": "某公司发布第三季度财报 营收增长15%",
        "content": (
            "某科技公司今日发布第三季度财报，营收同比增长15%，净利润达到3.2亿元。"
            "公司称增长主要来自云业务。"
        ),
        "source": "财经网",
        "url": "https://example.com/b1",
        "published_at": "2026-10-21T09:00:00+08:00",
        "rank": 2,
    },
]


def material_batch(*, failing: bool = False) -> dict:
    document = make_document(
        feed="rss", generator="test", items=[make_item(**item) for item in ITEMS]
    )
    sources = [
        {"type": "rss", "id": "good", "name": "Good Feed", "count": 2, "cached": False}
    ]
    if failing:
        sources.append(
            {
                "type": "rss",
                "id": "broken",
                "name": "Broken Feed",
                "count": 0,
                "cached": False,
                "error": "fetch failed: timed out",
            }
        )
    return {
        "generated_at": now_iso(),
        "cache_dir": "/tmp/cache",
        "max_age": 3600,
        "count": len(ITEMS),
        "documents": [document],
        "items": document["items"],
        "sources": sources,
    }


def build_service(tmp_path, *, failing: bool = False) -> NewsService:
    return NewsService(
        NewsConfig(enabled=True, root=str(tmp_path)),
        llm=FakeNewsLLM(),
        material_source=lambda **kwargs: material_batch(failing=failing),
    )


@pytest.mark.asyncio
async def test_startup_fails_a_run_left_running(tmp_path) -> None:
    store = NewsStore(tmp_path)
    record = RunRecord(
        id=new_run_id(),
        created_at=now_iso(),
        updated_at=now_iso(),
        status="running",
        stages=[
            StageRun(name="material", status="ok", count=2),
            StageRun(name="clean", status="running"),
            StageRun(name="aggregate", status="pending"),
        ],
    )
    store.save_run(record)

    service = build_service(tmp_path)
    await service.startup()

    loaded = service.get_run(record.id)
    assert loaded.status == "failed"
    assert "restarted" in loaded.error
    assert loaded.stages[1].status == "failed"
    assert loaded.stages[2].status == "skipped"


@pytest.mark.asyncio
async def test_startup_leaves_finished_runs_alone(tmp_path) -> None:
    service = build_service(tmp_path)
    await service.startup()
    run = await service.run_now()
    await service.startup()
    assert service.get_run(run.id).status == run.status


@pytest.mark.asyncio
async def test_source_health_accumulates_failures(tmp_path) -> None:
    service = build_service(tmp_path, failing=True)
    await service.startup()
    await service.run_now()
    await service.run_now()

    health = service.source_health()
    by_id = {entry["id"]: entry for entry in health["sources"]}
    assert set(by_id) == {"good", "broken"}
    assert by_id["good"]["last_status"] == "ok"
    assert by_id["good"]["consecutive_failures"] == 0
    assert by_id["broken"]["last_status"] == "error"
    assert by_id["broken"]["consecutive_failures"] == 2
    assert "timed out" in by_id["broken"]["last_error"]
    # Failing sources sort first so the console shows them without scrolling.
    assert health["sources"][0]["id"] == "broken"


@pytest.mark.asyncio
async def test_regenerate_bumps_the_revision_and_keeps_the_run(tmp_path) -> None:
    service = build_service(tmp_path)
    await service.startup()
    run = await service.run_now()
    entry = service.run_drafts(run.id)[0]
    event_id = entry["event_id"]
    assert entry["revision"] == 1

    draft, review = await service.regenerate_draft(run.id, event_id)

    assert draft.event_id == event_id
    assert draft.revision == 2
    assert review.event_id == event_id
    stored = service.store.load_draft(run.id, event_id)
    assert stored.revision == 2
    assert service.store.load_review(run.id, event_id) is not None
    assert service.get_run(run.id).status == run.status
    assert service.get_event(event_id).status in ("ready", "needs_review")


@pytest.mark.asyncio
async def test_regenerate_is_refused_while_a_run_is_active(tmp_path) -> None:
    service = build_service(tmp_path)
    await service.startup()
    run = await service.run_now()
    event_id = service.run_drafts(run.id)[0]["event_id"]

    service._active = "another-run"  # simulate a batch in flight
    try:
        with pytest.raises(RunBusy):
            await service.regenerate_draft(run.id, event_id)
    finally:
        service._active = None


@pytest.mark.asyncio
async def test_regenerate_rejects_an_unknown_event(tmp_path) -> None:
    service = build_service(tmp_path)
    await service.startup()
    run = await service.run_now()
    with pytest.raises(Exception, match="not in the event store"):
        await service.regenerate_draft(run.id, "evt_missing")
