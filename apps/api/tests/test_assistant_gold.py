from __future__ import annotations

import httpx

from winnie_api.main import create_app
from winnie_api.settings import Settings
from winnie_assistant.config import (
    AssistantConfig,
    GoldConfig,
    IndexConfig,
    MemoryConfig,
    SearchConfig,
    TasksConfig,
    WeatherConfig,
)

PAYLOAD = {
    "symbol": "XAU",
    "currency": "USD",
    "price": 3876.4,
    "updatedAt": "2026-10-31T22:28:00Z",
}


async def test_gold_skill_and_manual_check(tmp_path) -> None:
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        assistant=AssistantConfig(
            enabled=True,
            root=str(tmp_path / "assistant"),
            timezone="Asia/Shanghai",
            search=SearchConfig(provider="fake"),
            index=IndexConfig(enabled=False),
            memory=MemoryConfig(auto_distill=False),
            tasks=TasksConfig(enabled=False),
            weather=WeatherConfig(enabled=False),
            gold=GoldConfig(enabled=True, schedule="every 5m"),
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.gold-api.com":
            return httpx.Response(200, json=PAYLOAD)
        return httpx.Response(500)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(settings=settings, http=http)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                skills = await client.get("/api/v1/assistant/skills")
                assert skills.status_code == 200, skills.text
                by_name = {skill["name"]: skill for skill in skills.json()["skills"]}
                assert "gold" in by_name
                assert by_name["gold"]["available"] is True
                assert by_name["gold"]["tools"] == ["gold_price"]

                check = await client.post("/api/v1/assistant/gold/check")
                assert check.status_code == 200, check.text
                body = check.json()
                assert body["status"] == "ok"
                assert body["price"] == 3876.4
                assert body["move"] is None
                assert "3,876.40" in body["message"]
                assert "历史样本不足" in body["message"]

                status = await client.get("/api/v1/assistant/gold")
                assert status.status_code == 200
                assert status.json()["enabled"] is True
                assert status.json()["last_status"] == "ok"
                assert status.json()["last_price"] == 3876.4
                assert status.json()["push"] is False  # no notifier in this test

                overall = await client.get("/api/v1/assistant/status")
                assert overall.json()["gold"]["enabled"] is True
    finally:
        await http.aclose()


async def test_gold_is_absent_when_disabled(tmp_path) -> None:
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        assistant=AssistantConfig(
            enabled=True,
            root=str(tmp_path / "assistant"),
            search=SearchConfig(provider="fake"),
            index=IndexConfig(enabled=False),
            memory=MemoryConfig(auto_distill=False),
            tasks=TasksConfig(enabled=False),
            weather=WeatherConfig(enabled=False),
            gold=GoldConfig(enabled=False),
        ),
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
                skills = await client.get("/api/v1/assistant/skills")
                by_name = {skill["name"]: skill for skill in skills.json()["skills"]}
                assert by_name["gold"]["available"] is False
                check = await client.post("/api/v1/assistant/gold/check")
                assert check.status_code == 409
    finally:
        await http.aclose()
