from __future__ import annotations

from pathlib import Path

import httpx

from winnie_api.main import create_app
from winnie_api.settings import DatabaseConfig, Settings
from winnie_core.config import AuthConfig, JobsConfig


async def test_readyz_fails_when_database_is_unavailable(
    tmp_path: Path,
) -> None:
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        auth=AuthConfig(enabled=True),
        database=DatabaseConfig(
            url="sqlite+aiosqlite:////dev/null/winnie.db",
            auto_create_tables=False,
        ),
        jobs=JobsConfig(backend="inline"),
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    app = create_app(settings=settings, http=http)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/readyz")
                assert response.status_code == 503
                assert response.json() == {"detail": "database unavailable"}
    finally:
        await http.aclose()


async def test_readyz_fails_when_queue_is_unreachable(
    meeting_settings: Settings,
    monkeypatch,
) -> None:
    settings = meeting_settings.model_copy(
        update={
            "jobs": JobsConfig(
                backend="arq", redis_url="redis://127.0.0.1:1/0"
            )
        }
    )

    async def fail_ping(self) -> None:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("winnie_api.main.ArqJobQueue.ping", fail_ping)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    app = create_app(settings=settings, http=http)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/readyz")
                assert response.status_code == 503
                assert response.json() == {"detail": "queue unavailable"}
    finally:
        await http.aclose()
