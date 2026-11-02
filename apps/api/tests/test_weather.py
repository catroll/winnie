from __future__ import annotations

import json
from datetime import datetime, timedelta
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
    ForecastCache,
    WeatherCheck,
    WeatherError,
    WeatherService,
    WeatherWatch,
    forecast_cache_key,
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


async def test_a_repeat_forecast_is_served_from_the_cache(tmp_path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=FORECAST)

    cache = ForecastCache(tmp_path / "weather-cache.json")
    service = WeatherService(
        WeatherConfig(), http=_service(handler).http, timezone=TZ, cache=cache
    )
    first = await service.check()
    second = await service.check()

    assert len(seen) == 1
    assert second.report.fetched_at == first.report.fetched_at
    assert second.message == first.message
    assert cache.path.exists()


def test_forecast_cache_expires_and_is_scoped_to_the_day(tmp_path) -> None:
    cache = ForecastCache(tmp_path / "weather-cache.json")
    now = datetime.now(TZ)
    report = _report()
    key = forecast_cache_key(31.23, 121.47, "上海", now)

    cache.put(key, report, now=now)
    assert cache.get(key, now=now) is not None
    # The same question 31 minutes later is a refetch.
    assert cache.get(key, now=now + timedelta(minutes=31)) is None
    # Tomorrow's key is a different question, even with the same coordinates.
    assert cache.get(forecast_cache_key(31.23, 121.47, "上海", now + timedelta(days=1)),
                     now=now) is None
    # A different place does not share the entry.
    assert cache.get(forecast_cache_key(30.59, 114.31, "武汉", now), now=now) is None


async def test_a_corrupt_cache_file_is_ignored(tmp_path) -> None:
    path = tmp_path / "weather-cache.json"
    path.write_text("{not json", encoding="utf-8")
    service = WeatherService(
        WeatherConfig(),
        http=_service().http,
        timezone=TZ,
        cache=ForecastCache(path),
    )
    check = await service.check()
    assert check.report.tomorrow.date == "2026-11-02"
    assert path.read_text(encoding="utf-8").startswith("{")


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


class _FakePhone:
    """A phone that filters its fixes by ``since``, as the real query does."""

    def __init__(self, fixes: list[dict]) -> None:
        self.fixes = fixes
        self.windows: list[datetime | None] = []

    async def locations(self, *, since=None, until=None, limit=100):
        self.windows.append(since)
        if since is None:
            return list(self.fixes)[:limit]
        return [fix for fix in self.fixes if fix["captured_at"] >= since][:limit]


class _BrokenPhone:
    async def locations(self, *, since=None, until=None, limit=100):
        raise RuntimeError("phone storage is down")


def _watch(
    tmp_path,
    *,
    registry=None,
    service=None,
    state=None,
    webhook="",
    phone=None,
    follow_phone=True,
):
    state_path = tmp_path / "state.json"
    if state is not None:
        state_path.write_text(json.dumps(state), encoding="utf-8")
    config = WeatherConfig(
        schedule="daily 07:00",
        webhook=webhook,
        location="上海",
        follow_phone=follow_phone,
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
            phone=phone,
        ),
        state_path,
    )


class _RecordingService(_FakeService):
    """Records the arguments and labels its report with the place it was given."""

    def __init__(self) -> None:
        super().__init__()
        self.calls_with: list[dict] = []

    async def check(self, **kwargs):
        self.calls_with.append(kwargs)
        base = await super().check()
        label = str(kwargs.get("location") or "上海")
        return WeatherCheck(
            report=parse_open_meteo({"daily": FORECAST["daily"]}, label, TZ),
            advisories=base.advisories,
            message=base.message,
            fingerprint=base.fingerprint,
        )


def _fix(minutes_ago: int, **extra) -> dict:
    moment = datetime(2026, 11, 1, 7, 0, tzinfo=TZ)
    from datetime import timedelta

    return {
        "captured_at": moment - timedelta(minutes=minutes_ago),
        "latitude": 30.43759,
        "longitude": 114.43433,
        "city": "武汉市",
        "district": "江夏区",
        "address": "湖北省武汉市江夏区藏龙岛街道天纵水晶郦都",
        **extra,
    }


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


async def test_check_uses_a_per_call_location_and_geocodes_it() -> None:
    """The memory's default city must reach the forecast, not just the prompt."""
    seen: list[httpx.Request] = []
    geocode = {
        "results": [
            {
                "name": "武汉",
                "latitude": 30.58333,
                "longitude": 114.26667,
                "admin1": "湖北",
                "country": "中国",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json=geocode)
        return httpx.Response(200, json=FORECAST)

    service = _service(handler)
    check = await service.check(location="武汉")

    assert seen[0].url.host == "geocoding-api.open-meteo.com"
    assert seen[0].url.params["name"] == "武汉"
    assert seen[1].url.params["latitude"] == "30.58333"
    assert seen[1].url.params["longitude"] == "114.26667"
    assert check.report.location == "武汉（湖北）"


async def test_check_accepts_explicit_coordinates_without_geocoding() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=FORECAST)

    service = _service(handler)
    check = await service.check(location="公司", latitude=30.5, longitude=114.3)

    assert all(request.url.host != "geocoding-api.open-meteo.com" for request in seen)
    assert seen[0].url.params["latitude"] == "30.5"
    assert check.report.location == "公司"


async def test_check_without_an_override_keeps_the_configured_place() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=FORECAST)

    service = _service(handler, config=WeatherConfig(location="上海"))
    check = await service.check()

    assert seen[0].url.params["latitude"] == "31.2304"
    assert check.report.location == "上海"


async def test_unknown_place_is_reported_not_ignored() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json=FORECAST)

    with pytest.raises(WeatherError, match="no place called"):
        await _service(handler).check(location="不存在的城市")


async def test_weather_tool_passes_the_location_through() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "武汉",
                            "latitude": 30.58333,
                            "longitude": 114.26667,
                        }
                    ]
                },
            )
        return httpx.Response(200, json=FORECAST)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    toolbox = ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=http,
        tasks_enabled=False,
        weather=_service(handler),
    )
    output = await toolbox.execute(
        "weather_forecast", {"location": "武汉"}, conversation_id="c"
    )
    assert output.startswith("武汉")


async def test_the_scheduled_check_follows_a_fresh_phone_fix(tmp_path) -> None:
    """The owner's rule applies to the push too, not only to chat."""
    service = _RecordingService()
    phone = _FakePhone([_fix(10)])
    watch, state_path = _watch(tmp_path, service=service, phone=phone)

    run = await watch.run_now()

    assert run.status == "ok"
    assert service.calls_with == [
        {
            "location": "武汉市江夏区",
            "latitude": 30.43759,
            "longitude": 114.43433,
        }
    ]
    # The query window is the configured freshness bound.
    assert phone.windows == [datetime(2026, 11, 1, 5, 0, tzinfo=TZ)]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_location"] == "武汉市江夏区"
    # The notice names the place the forecast is for, not the config default.
    assert watch.store.list()[0].title == "天气提醒 · 武汉市江夏区"


async def test_a_stale_fix_falls_back_to_the_configured_place(tmp_path) -> None:
    service = _RecordingService()
    phone = _FakePhone([_fix(180)])
    watch, _ = _watch(tmp_path, service=service, phone=phone)

    await watch.run_now()

    assert service.calls_with == [{}]


async def test_no_fix_falls_back_to_the_configured_place(tmp_path) -> None:
    service = _RecordingService()
    watch, _ = _watch(tmp_path, service=service, phone=_FakePhone([]))

    await watch.run_now()

    assert service.calls_with == [{}]


async def test_a_broken_phone_does_not_cost_the_notice(tmp_path) -> None:
    service = _RecordingService()
    watch, _ = _watch(tmp_path, service=service, phone=_BrokenPhone())

    run = await watch.run_now()

    assert run.status == "ok"
    assert service.calls_with == [{}]


async def test_follow_phone_can_be_turned_off(tmp_path) -> None:
    service = _RecordingService()
    phone = _FakePhone([_fix(5)])
    watch, _ = _watch(
        tmp_path, service=service, phone=phone, follow_phone=False
    )

    await watch.run_now()

    assert phone.windows == []
    assert service.calls_with == [{}]


def test_the_place_label_prefers_city_and_district() -> None:
    from winnie_assistant.weather import _place_label

    assert _place_label({"city": "武汉市", "district": "江夏区"}) == "武汉市江夏区"
    assert _place_label({"city": "武汉市"}) == "武汉市"
    assert (
        _place_label({"city": "", "address": "湖北省武汉市江夏区某路"})
        == "湖北省武汉市江夏区某路"
    )
    assert _place_label({"latitude": 30.5, "longitude": 114.3}) == "30.5000,114.3000"
