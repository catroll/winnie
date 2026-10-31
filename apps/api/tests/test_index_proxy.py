from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from winnie_api.main import create_app
from winnie_api.settings import IndexConfig, Settings


class FakeIndex:
    """Answers the daemon's management paths and records what it was sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.failure: Exception | None = None
        self.reject = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        path = request.url.path
        if path == "/admin/status":
            return httpx.Response(200, json={"documents": {"indexed": 2}, "chunks": 9})
        if path == "/admin/config" and request.method == "GET":
            return httpx.Response(
                200, json={"path": "/tmp/config.yaml", "config": {"roots": []}}
            )
        if path == "/admin/config" and request.method == "PUT":
            return httpx.Response(200, json={"config": {"roots": [{"name": "extra"}]}})
        if path == "/admin/search":
            return httpx.Response(200, json={"query": "x", "hits": [], "files": []})
        if path == "/admin/build":
            if self.reject:
                return httpx.Response(400, json={"error": "build refused"})
            return httpx.Response(200, json={"build": {"added": 1, "dry_run": False}})
        if path == "/admin/skill":
            return httpx.Response(200, json={"ok": True, "path": "/tmp/SKILL.md"})
        if path == "/admin/broken":
            return httpx.Response(400, json={"error": "unknown key(s): bogus"})
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def fake_index() -> FakeIndex:
    return FakeIndex()


@pytest.fixture
def index_settings() -> Settings:
    return Settings(
        log_root="/tmp/winnie-logs",
        routes={},
        index=IndexConfig(enabled=True, base_url="http://index.local:8787"),
    )


@pytest_asyncio.fixture
async def index_client(
    index_settings: Settings, fake_index: FakeIndex
) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake_index), timeout=5.0)
    app = create_app(settings=index_settings, http=http)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    await http.aclose()


async def test_status_is_proxied(index_client: httpx.AsyncClient) -> None:
    response = await index_client.get("/api/v1/index/status")
    assert response.status_code == 200
    assert response.json()["chunks"] == 9


async def test_config_get_and_put(index_client: httpx.AsyncClient) -> None:
    config = await index_client.get("/api/v1/index/config")
    assert config.json()["path"] == "/tmp/config.yaml"
    saved = await index_client.put(
        "/api/v1/index/config", json={"config": {"roots": [{"name": "extra", "path": "/tmp"}]}}
    )
    assert saved.json()["config"]["roots"][0]["name"] == "extra"


async def test_search_is_proxied(index_client: httpx.AsyncClient) -> None:
    response = await index_client.post("/api/v1/index/search", json={"query": "x"})
    assert response.status_code == 200
    assert response.json()["hits"] == []


async def test_build_and_skill(index_client: httpx.AsyncClient) -> None:
    build = await index_client.post("/api/v1/index/build", json={"dry_run": False})
    assert build.json()["build"]["added"] == 1
    skill = await index_client.post("/api/v1/index/skill", json={"action": "install"})
    assert skill.json()["ok"] is True


async def test_daemon_down_becomes_503(
    index_client: httpx.AsyncClient, fake_index: FakeIndex
) -> None:
    fake_index.failure = httpx.ConnectError("connection refused")
    response = await index_client.get("/api/v1/index/status")
    assert response.status_code == 503
    assert "not reachable" in response.json()["detail"]


async def test_upstream_error_detail_is_preserved(
    index_client: httpx.AsyncClient, fake_index: FakeIndex
) -> None:
    fake_index.reject = True
    response = await index_client.post("/api/v1/index/build", json={})
    assert response.status_code == 400
    assert response.json()["detail"] == "build refused"


async def test_disabled_index_is_not_mounted() -> None:
    settings = Settings(
        log_root="/tmp/winnie-logs",
        routes={},
        index=IndexConfig(enabled=False),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/index/status")
            assert response.status_code == 404
    await http.aclose()
