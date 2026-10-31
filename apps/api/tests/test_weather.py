from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from winnie_assistant.config import WeatherConfig
from winnie_assistant.conversations import ConversationStore
from winnie_assistant.search import FakeSearchProvider
from winnie_assistant.tools import ToolBox
from winnie_assistant.weather import (
    Advisory,
    WeatherCheck,
    WeatherError,
    WeatherService,
    WeatherWatch,
    parse_open_meteo,
)

TZ = ZoneInfo("Asia/Shanghai")

FORECAST = {
    "latitude": 31.247803,
    "longitude": 121.5,
    "timezone": "Asia/Shanghai",
    "daily_units": {"time": "iso8601", "temperature_2m_max": "°C"},
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


def _report(daily: dict | None = None):
    return parse_open_meteo({"daily": daily or FORECAST["daily"]}, "上海", TZ)


def _service(handler=None, *, config: WeatherConfig | None = None) -> WeatherService:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            handler or (lambda request: httpx.Response(200, json=FORECAST))
        )
    )
    return WeatherService(config or WeatherConfig(), http=http, timezone=TZ)


def _day(**overrides):
    base = {
        "time": ["2026-10-31", "2026-11-01", "2026-11-02"],
        "weather_code": [3, 3, 3],
        "temperature_2m_max": [20.0, 22.0, 23.0],
        "temperature_2m_min": [10.0, 11.0, 12.0],
        "precipitation_sum": [0.0, 0.0, 0.0],
        "precipitation_probability_max": [0, 0, 0],
        "snowfall_sum": [0.0, 0.0, 0.0],
        "wind_speed_10m_max": [5.0, 5.0, 5.0],
    }
    base.update(overrides)
    return base


def test_parse_maps_yesterday_today_and_tomorrow() -> None:
    report = _report()
    assert report.yesterday is not None
    assert report.yesterday.date == "2026-10-31"
    assert report.today.date == "2026-11-01"
    assert report.today.description == "小雨"
    assert report.today.temperature_max == 26.6
    assert report.tomorrow.precipitation_probability == 86


def test_rain_and_temperature_drop_are_reported() -> None:
    service = _service()
    check = service.evaluate(_report())

    kinds = [advisory.kind for advisory in check]
    assert kinds == ["rain", "temperature_drop"]
    assert "带伞" in check[0].text
    assert "加衣" in check[1].text


def test_snow_uses_the_snow_advice() -> None:
    daily = _day(
        weather_code=[71, 71, 71],
        temperature_2m_max=[0.0, -1.0, -2.0],
        temperature_2m_min=[-8.0, -9.0, -10.0],
        snowfall_sum=[2.0, 3.0, 1.0],
    )
    advisories = _service().evaluate(_report(daily))
    kinds = [advisory.kind for advisory in advisories]
    assert "snow" in kinds
    assert "snow_tomorrow" in kinds
    assert all("防滑" in advisory.text for advisory in advisories[:2])


def test_temperature_rise_and_heat_and_wind_and_severe() -> None:
    daily = _day(
        weather_code=[3, 95, 95],
        temperature_2m_max=[20.0, 35.0, 41.0],
        temperature_2m_min=[10.0, 28.0, 30.0],
        wind_speed_10m_max=[10.0, 60.0, 60.0],
    )
    kinds = [advisory.kind for advisory in _service().evaluate(_report(daily))]
    assert "temperature_rise" in kinds
    assert "heat" in kinds
    assert "wind" in kinds
    assert "severe" in kinds


def test_calm_weather_has_no_advisories() -> None:
    daily = _day()
    advisories = _service().evaluate(_report(daily))
    assert advisories == []
    message = _service().render(_report(daily), advisories)
    assert "没有需要特别提醒" in message


def test_render_contains_advice_and_source() -> None:
    service = _service()
    report = _report()
    message = service.render(report, service.evaluate(report))
    assert "上海 · 2026-11-01" in message
    assert "Open-Meteo" in message
    assert "今天有雨" in message
    assert "明天最高温比今天低 6.6°C" in message


async def test_fetch_requests_open_meteo_and_parses_it() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=FORECAST)

    service = _service(handler)
    check = await service.check()

    assert seen[0].url.host == "api.open-meteo.com"
    assert seen[0].url.path == "/v1/forecast"
    assert seen[0].url.params["latitude"] == "31.2304"
    assert seen[0].url.params["timezone"] == "Asia/Shanghai"
    assert check.fingerprint == "2026-11-01:rain,temperature_drop"
    assert "带伞" in check.message


async def test_fetch_errors_are_reported() -> None:
    service = _service(lambda request: httpx.Response(503, text="busy"))
    with pytest.raises(WeatherError, match="HTTP 503"):
        await service.check()

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(WeatherError, match="unreachable|failed"):
        await _service(broken).check()


async def test_weather_tool_returns_the_message_and_respects_disabling() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=FORECAST))
    )
    toolbox = ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=http,
        tasks_enabled=False,
        weather=_service(),
    )
    output = await toolbox.execute("weather_forecast", {}, conversation_id="c")
    assert "带伞" in output
    assert "加衣" in output

    disabled = ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=http,
        tasks_enabled=False,
    )
    error = await disabled.execute("weather_forecast", {}, conversation_id="c")
    assert error.startswith("ERROR: weather is disabled")


class _Registry:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def views(self):
        return [
            SimpleNamespace(name="weather", enabled=self.enabled, available=True)
        ]


class _FakeService:
    def __init__(self) -> None:
        self.calls = 0
        self.check_result = WeatherCheck(
            report=_report(),
            advisories=(Advisory("rain", "今天有雨，出门记得带伞。"),),
            message="今天有雨，出门记得带伞。",
            fingerprint="2026-11-01:rain",
        )

    async def check(self):
        self.calls += 1
        return self.check_result


def _watch(tmp_path, *, registry=None, service=None, state=None, webhook=""):
    state_path = tmp_path / "state.json"
    if state is not None:
        state_path.write_text(json.dumps(state), encoding="utf-8")
    config = WeatherConfig(
        schedule="daily 07:00",
        webhook=webhook,
        location="上海",
    )
    clock = lambda: datetime(2026, 11, 1, 7, 0, tzinfo=TZ)
    return (
        WeatherWatch(
            config=config,
            service=service or _FakeService(),
            store=ConversationStore(tmp_path / "conversations"),
            registry=registry or _Registry(),
            timezone=TZ,
            state_path=state_path,
            clock=clock,
            tick_seconds=3600,
        ),
        state_path,
    )


async def test_tick_records_a_notice_once_per_advice(tmp_path) -> None:
    watch, _ = _watch(
        tmp_path, state={"next_run": "2026-11-01T06:00:00+08:00"}
    )
    run = await watch.tick()
    assert run is not None and run.status == "ok"
    assert run.advisories == ("rain",)
    conversations = watch.store.list()
    assert len(conversations) == 1
    assert conversations[0].source == "weather"

    again = await watch.run_now(force=False)
    assert again.status == "duplicate"
    assert len(watch.store.list()) == 1

    assert (await watch.tick()) is None


async def test_disabled_skill_skips_the_check(tmp_path) -> None:
    service = _FakeService()
    watch, _ = _watch(
        tmp_path,
        registry=_Registry(enabled=False),
        service=service,
        state={"next_run": "2026-11-01T06:00:00+08:00"},
    )
    run = await watch.tick()
    assert run is not None and run.status == "disabled"
    assert service.calls == 0
    assert watch.store.list() == []


async def test_webhook_delivery_and_manual_run_keep_the_schedule(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    watch, state_path = _watch(
        tmp_path,
        state={"next_run": "2026-11-01T06:00:00+08:00"},
        webhook="https://hook.example/weather",
    )
    watch.http = http
    try:
        run = await watch.run_now(force=True)
    finally:
        await http.aclose()

    assert run.status == "ok"
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["kind"] == "weather"
    assert body["conversation_id"] == run.conversation_id
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["next_run"] == "2026-11-01T06:00:00+08:00"
