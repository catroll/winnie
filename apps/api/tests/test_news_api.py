"""The news API: a run through the real router, service and AI binding."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from winnie_llm import BindingDraft, IntegrationDraft, VendorDraft
from winnie_api.main import create_app
from winnie_assistant.config import AssistantConfig, SearchConfig as AssistantSearchConfig
from winnie_api.settings import AIConfig, Settings
from winnie_gateway.config import RouteConfig
from winnie_news.config import NewsConfig

FIXTURES = [
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


def material_batch() -> dict:
    from winnie_news.common.material import make_document, make_item

    document = make_document(
        feed="rss",
        generator="test",
        items=[make_item(**item) for item in FIXTURES],
    )
    return {
        "generated_at": "2026-10-21T10:00:00+08:00",
        "cache_dir": "/tmp/cache",
        "max_age": 3600,
        "count": len(document["items"]),
        "documents": [document],
        "items": document["items"],
        "sources": [
            {"type": "rss", "id": "test", "name": "Test Feed", "count": 2, "cached": False}
        ],
    }


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={
            "openai": RouteConfig(
                protocol="openai", base_url="https://api.openai.com"
            )
        },
        ai=AIConfig(enabled=True, path=str(tmp_path / "ai.yaml")),
        # The search stage must not reach the network in a test; the fake
        # provider is the assistant's documented offline option.
        assistant=AssistantConfig(
            enabled=True,
            root=str(tmp_path / "assistant"),
            search=AssistantSearchConfig(provider="fake"),
        ),
        news=NewsConfig(enabled=True, root=str(tmp_path / "news")),
    )


@asynccontextmanager
async def opened(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0)
    )
    app = create_app(settings=_settings(tmp_path), http=http)
    async with app.router.lifespan_context(app):
        service = app.state.news_service
        assert service is not None
        # No fake supplier ships. This test configures an explicit test-only
        # fake model for the news features.
        ai = app.state.ai_settings
        ai.create_vendor(
            VendorDraft(
                id="test-tools",
                label="Test tools",
                models=[{"id": "fake"}],
            )
        )
        ai.create_integration(
            IntegrationDraft(
                name="fake-news",
                label="Test fake",
                vendor="test-tools",
                kind="fake",
                gateway=False,
            )
        )
        for feature in ("news.extract", "news.write", "news.review"):
            ai.set_binding(
                feature,
                BindingDraft(vendor="test-tools", model="fake"),
            )
        # The collectors are the only network dependency left in this path.
        service._material_source = lambda **kwargs: material_batch()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    await http.aclose()


async def test_status_and_a_full_run(tmp_path: Path) -> None:
    async with opened(tmp_path) as client:
        status = (await client.get("/api/v1/news/status")).json()
        assert status["enabled"] is True
        assert status["ready"] is True
        assert status["llm"] == "fake-news"
        assert status["active_run"] is None

        started = await client.post("/api/v1/news/runs", json={})
        assert started.status_code == 202
        run = started.json()
        assert run["status"] == "running"

        for _ in range(100):
            current = (await client.get(f"/api/v1/news/runs/{run['id']}")).json()
            if current["status"] != "running":
                break
            await asyncio.sleep(0.05)
        assert current["status"] == "done"
        assert {stage["name"] for stage in current["stages"]} == {
            "material",
            "clean",
            "aggregate",
            "search",
            "generate",
            "review",
        }

        drafts = (await client.get(f"/api/v1/news/runs/{run['id']}/drafts")).json()
        assert drafts["drafts"]
        event_id = drafts["drafts"][0]["event_id"]

        detail = (
            await client.get(f"/api/v1/news/runs/{run['id']}/drafts/{event_id}")
        ).json()
        assert detail["draft"]["schema"] == "winnie.news.draft/v1"
        assert detail["review"]["schema"] == "winnie.news.review/v1"

        markdown = await client.get(
            f"/api/v1/news/runs/{run['id']}/drafts/{event_id}/markdown"
        )
        assert markdown.status_code == 200
        assert markdown.text.startswith("# ")
        assert "## Sources" in markdown.text

        events = (await client.get("/api/v1/news/events")).json()["events"]
        assert len(events) == current["stats"]["events"]
        single = (await client.get(f"/api/v1/news/events/{events[0]['id']}")).json()
        assert single["id"] == events[0]["id"]
        assert single["articles"]


async def test_unknown_run_and_event_are_404(tmp_path: Path) -> None:
    async with opened(tmp_path) as client:
        assert (await client.get("/api/v1/news/runs/nope")).status_code == 404
        assert (await client.get("/api/v1/news/events/evt_nope")).status_code == 404


async def test_a_second_run_reports_its_own_record(tmp_path: Path) -> None:
    async with opened(tmp_path) as client:
        first = (await client.post("/api/v1/news/runs", json={})).json()
        for _ in range(100):
            if (await client.get(f"/api/v1/news/runs/{first['id']}")).json()[
                "status"
            ] != "running":
                break
            await asyncio.sleep(0.05)

        second = (await client.post("/api/v1/news/runs", json={})).json()
        for _ in range(100):
            if (await client.get(f"/api/v1/news/runs/{second['id']}")).json()[
                "status"
            ] != "running":
                break
            await asyncio.sleep(0.05)

        runs = (await client.get("/api/v1/news/runs")).json()
        assert {record["id"] for record in runs} == {first["id"], second["id"]}
        # The second batch holds the same articles, so nothing is drafted again.
        detail = (await client.get(f"/api/v1/news/runs/{second['id']}")).json()
        assert detail["stats"]["drafts"] == 0


async def test_the_assistant_offers_the_news_skill(tmp_path: Path) -> None:
    async with opened(tmp_path) as client:
        skills = (await client.get("/api/v1/assistant/skills")).json()["skills"]
        news = next(skill for skill in skills if skill["name"] == "news")
        assert news["available"] is True
        assert news["scheduled_safe"] is True
        assert "news_run" in news["tools"]
        assert "news_read_draft" in news["tools"]


async def test_the_news_tools_operate_the_service(tmp_path: Path) -> None:
    from winnie_assistant.search import FakeSearchProvider
    from winnie_assistant.tools import ToolBox
    from winnie_news.fake import FakeNewsLLM
    from winnie_news.service import NewsService

    from winnie_api.news.gateway import NewsGatewayAdapter

    service = NewsService(
        NewsConfig(enabled=True, root=str(tmp_path / "news")),
        llm=FakeNewsLLM(),
        material_source=lambda **kwargs: material_batch(),
    )
    await service.startup()
    toolbox = ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=httpx.AsyncClient(),
        news=NewsGatewayAdapter(service),
    )
    try:
        status = await toolbox.execute("news_status", {}, conversation_id="c1")
        assert "ready: True" in status

        started = await toolbox.execute("news_run", {}, conversation_id="c1")
        assert "Started news run" in started
        await service._task

        progress = await toolbox.execute("news_run_status", {}, conversation_id="c1")
        assert "status" in progress or "done" in progress
        assert "generate=" in progress

        drafts = await toolbox.execute("news_drafts", {}, conversation_id="c1")
        assert "review" in drafts
        event_id = next(
            line.split(":")[0][2:]
            for line in drafts.splitlines()
            if line.startswith("- ")
        )

        markdown = await toolbox.execute(
            "news_read_draft", {"event_id": event_id}, conversation_id="c1"
        )
        assert markdown.startswith("# ")
    finally:
        await toolbox.http.aclose()


async def test_source_health_and_draft_regeneration(tmp_path: Path) -> None:
    async with opened(tmp_path) as client:
        started = await client.post("/api/v1/news/runs", json={})
        run = started.json()
        for _ in range(100):
            current = (await client.get(f"/api/v1/news/runs/{run['id']}")).json()
            if current["status"] != "running":
                break
            await asyncio.sleep(0.05)

        sources = (await client.get("/api/v1/news/sources")).json()
        assert sources["updated_at"]
        health = {entry["id"]: entry for entry in sources["sources"]}
        assert health["test"]["last_status"] == "ok"
        assert health["test"]["last_count"] == 2

        drafts = (await client.get(f"/api/v1/news/runs/{run['id']}/drafts")).json()
        event_id = drafts["drafts"][0]["event_id"]
        assert drafts["drafts"][0]["revision"] == 1

        regenerated = await client.post(
            f"/api/v1/news/runs/{run['id']}/drafts/{event_id}/regenerate"
        )
        assert regenerated.status_code == 200
        body = regenerated.json()
        assert body["draft"]["revision"] == 2
        assert body["review"]["event_id"] == event_id

        after = (await client.get(f"/api/v1/news/runs/{run['id']}/drafts")).json()
        entry = next(item for item in after["drafts"] if item["event_id"] == event_id)
        assert entry["revision"] == 2


async def test_regeneration_of_a_missing_draft_is_404(tmp_path: Path) -> None:
    async with opened(tmp_path) as client:
        started = await client.post("/api/v1/news/runs", json={})
        run = started.json()
        for _ in range(100):
            if (await client.get(f"/api/v1/news/runs/{run['id']}")).json()[
                "status"
            ] != "running":
                break
            await asyncio.sleep(0.05)
        response = await client.post(
            f"/api/v1/news/runs/{run['id']}/drafts/evt_nope/regenerate"
        )
        assert response.status_code == 404
