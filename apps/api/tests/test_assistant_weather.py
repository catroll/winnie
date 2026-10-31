from __future__ import annotations

import httpx

from winnie_api.main import create_app
from winnie_api.settings import Settings
from winnie_assistant.config import (
    AssistantConfig,
    IndexConfig,
    MemoryConfig,
    SearchConfig,
    TasksConfig,
    WeatherConfig,
)

FORECAST = {
    "latitude": 31.247803,
    "longitude": 121.5,
    "timezone": "Asia/Shanghai",
    "daily": {
        "time": ["2026-10-31", "2026-11-01", "2026-11-02"],
        "weather_code": [45, 61, 61],
        "temperature_2m_max": [23.0, 26.6, 20.0],
        "temperature_2m_min": [12.0, 15.7, 13.4],
        "precipitation_sum": [0.0, 1.5, 2.4],
        "precipitation_probability_max": [0, 92, 86],
        "snowfall_sum": [0.0, 0.0, 0.0],
        "wind_speed_10m_max": [14.0, 18.6, 10.7],
    },
}


async def test_weather_skill_and_manual_check(tmp_path) -> None:
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
            weather=WeatherConfig(enabled=True, schedule="daily 07:00"),
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(200, json=FORECAST)
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
                assert "weather" in by_name
                assert by_name["weather"]["available"] is True
                assert by_name["weather"]["enabled"] is True
                assert "weather_forecast" in by_name["weather"]["tools"]

                check = await client.post("/api/v1/assistant/weather/check")
                assert check.status_code == 200, check.text
                body = check.json()
                assert body["status"] == "ok"
                assert "带伞" in body["message"]
                assert "加衣" in body["message"]
                assert body["advisories"] == ["rain", "temperature_drop"]
                assert body["conversation_id"]

                conversation = await client.get(
                    f"/api/v1/assistant/conversations/{body['conversation_id']}"
                )
                assert conversation.status_code == 200
                assert conversation.json()["source"] == "weather"
                assert conversation.json()["messages"][0]["role"] == "assistant"
                assert "Open-Meteo" in conversation.json()["messages"][0]["text"]

                listing = await client.get("/api/v1/assistant/conversations")
                assert listing.json()["total"] == 1

                weather = await client.get("/api/v1/assistant/weather")
                assert weather.status_code == 200
                assert weather.json()["location"] == "上海"
                assert weather.json()["last_status"] == "ok"
                assert weather.json()["next_run"]

                offline = await client.put(
                    "/api/v1/assistant/skills/weather", json={"enabled": False}
                )
                assert offline.status_code == 200
                assert offline.json()["enabled"] is False
                # A manual check is a diagnostic action, not a skill tool.
                assert (
                    await client.post("/api/v1/assistant/weather/check")
                ).status_code == 200

                online = await client.put(
                    "/api/v1/assistant/skills/weather", json={"enabled": True}
                )
                assert online.json()["enabled"] is True
    finally:
        await http.aclose()


async def test_weather_is_absent_when_disabled(tmp_path) -> None:
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
                assert by_name["weather"]["available"] is False
                check = await client.post("/api/v1/assistant/weather/check")
                assert check.status_code == 409
    finally:
        await http.aclose()
