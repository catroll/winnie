"""Weather watch: forecast, rules, and the scheduled notification.

The feature is deliberately deterministic. A forecast is fetched, a small set of
rules turns it into advisories (umbrella, clothing, heat, wind, severe weather)
and only an advisory that changed since the last check is delivered. No model
call is involved, so a 07:00 check costs nothing and cannot hallucinate.

Official warning feeds are not used: the keyless Chinese providers reject
requests (weather.com.cn alarm endpoints return 403) and the commercial ones
need a key. Severe conditions are therefore derived from the WMO weather code
and thresholds, and the message says where the data came from.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from winnie_assistant.config import WeatherConfig
from winnie_assistant.conversations import ConversationStore
from winnie_assistant.phone import PhoneGateway
from winnie_assistant.files import atomic_write
from winnie_assistant.scheduler import Schedule, parse_schedule

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
SOURCE_NAME = "Open-Meteo"
# A repeat question inside this window gains nothing from a refetch: the model
# runs that back the forecast refresh a few times a day, not every 30 minutes.
CACHE_TTL = timedelta(minutes=30)
CACHE_MAX_ENTRIES = 8

# WMO weather interpretation codes.
RAIN_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82})
SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})
SEVERE_CODES = frozenset({65, 67, 75, 82, 86, 95, 96, 99})

DESCRIPTIONS = {
    0: "晴",
    1: "晴间多云",
    2: "多云",
    3: "阴",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴冰雹",
    99: "强雷暴伴冰雹",
}


class WeatherError(RuntimeError):
    """The forecast could not be fetched or parsed."""


@dataclass(frozen=True)
class ForecastDay:
    date: str
    code: int
    temperature_max: float
    temperature_min: float
    precipitation: float
    precipitation_probability: int
    snowfall: float
    wind_max: float

    @property
    def description(self) -> str:
        return DESCRIPTIONS.get(self.code, f"天气代码 {self.code}")


@dataclass(frozen=True)
class WeatherReport:
    location: str
    fetched_at: datetime
    yesterday: ForecastDay | None
    today: ForecastDay
    tomorrow: ForecastDay


@dataclass(frozen=True)
class Advisory:
    kind: str
    text: str
    severity: str = "attention"


@dataclass(frozen=True)
class WeatherCheck:
    report: WeatherReport
    advisories: tuple[Advisory, ...]
    message: str
    fingerprint: str


def forecast_cache_key(
    latitude: float, longitude: float, location: str, now: datetime
) -> str:
    """The question, not the wording: place, rounded coordinates, local date."""
    return (
        f"{round(latitude, 2):.2f}|{round(longitude, 2):.2f}|"
        f"{location.strip()}|{now.date().isoformat()}"
    )


class ForecastCache:
    """Forecasts kept in one JSON file, shared by conversations and restarts.

    The local date is part of the key, so "today" can never be answered with
    yesterday's forecast. ``CACHE_TTL`` bounds the age; expired entries are
    dropped on write and the file is capped at ``CACHE_MAX_ENTRIES``.
    """

    def __init__(self, path: Path, ttl: timedelta = CACHE_TTL) -> None:
        self.path = path
        self.ttl = ttl

    def get(self, key: str, *, now: datetime) -> WeatherReport | None:
        entry = self._load().get(key)
        if not isinstance(entry, dict):
            return None
        try:
            report = _report_from_json(entry["report"])
        except (KeyError, TypeError, ValueError):
            return None
        # A clock skew or an NTP step can make the entry look a few
        # microseconds in the future; that is not a reason to refetch.
        age = now - report.fetched_at
        if age < timedelta(0):
            age = timedelta(0)
        if age > self.ttl:
            return None
        return report

    def put(self, key: str, report: WeatherReport, *, now: datetime) -> None:
        entries = self._load()
        for stored, entry in list(entries.items()):
            try:
                age = now - _report_from_json(entry["report"]).fetched_at
            except (KeyError, TypeError, ValueError):
                del entries[stored]
                continue
            if age < timedelta(0) or age > self.ttl:
                del entries[stored]
        entries[key] = {"report": _report_to_json(report)}
        while len(entries) > CACHE_MAX_ENTRIES:
            oldest = min(entries, key=lambda name: entries[name]["report"].get("fetched_at", ""))
            del entries[oldest]
        atomic_write(
            self.path,
            json.dumps({"version": 1, "entries": entries}, ensure_ascii=False),
        )

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        entries = data.get("entries") if isinstance(data, dict) else None
        return dict(entries) if isinstance(entries, dict) else {}


def _report_to_json(report: WeatherReport) -> dict[str, Any]:
    def day(value: ForecastDay | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "date": value.date,
            "code": value.code,
            "temperature_max": value.temperature_max,
            "temperature_min": value.temperature_min,
            "precipitation": value.precipitation,
            "precipitation_probability": value.precipitation_probability,
            "snowfall": value.snowfall,
            "wind_max": value.wind_max,
        }

    return {
        "location": report.location,
        "fetched_at": report.fetched_at.isoformat(),
        "yesterday": day(report.yesterday),
        "today": day(report.today),
        "tomorrow": day(report.tomorrow),
    }


def _report_from_json(data: Any) -> WeatherReport:
    if not isinstance(data, dict):
        raise ValueError("a cached report is not an object")
    fetched_at = datetime.fromisoformat(str(data["fetched_at"]))

    def day(value: Any) -> ForecastDay | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("a cached day is not an object")
        return ForecastDay(
            date=str(value["date"]),
            code=int(value["code"]),
            temperature_max=float(value["temperature_max"]),
            temperature_min=float(value["temperature_min"]),
            precipitation=float(value["precipitation"]),
            precipitation_probability=int(value["precipitation_probability"]),
            snowfall=float(value["snowfall"]),
            wind_max=float(value["wind_max"]),
        )

    if day(data["today"]) is None or day(data["tomorrow"]) is None:
        raise ValueError("a cached report needs today and tomorrow")
    return WeatherReport(
        location=str(data["location"]),
        fetched_at=fetched_at,
        yesterday=day(data["yesterday"]),
        today=day(data["today"]),
        tomorrow=day(data["tomorrow"]),
    )


class WeatherService:
    """Fetch one forecast and turn it into advisories."""

    def __init__(
        self,
        config: WeatherConfig,
        *,
        http: httpx.AsyncClient,
        timezone: ZoneInfo,
        cache: ForecastCache | None = None,
    ) -> None:
        self.config = config
        self.http = http
        self.timezone = timezone
        self.cache = cache

    async def check(
        self,
        *,
        location: str = "",
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> WeatherCheck:
        """One forecast.

        ``location``/``latitude``/``longitude`` override the configured place
        for this call. Without one the configured location is used, so the
        scheduled watcher is unchanged. The override is what lets a stored
        preference ("no location given → 武汉") actually reach the forecast
        instead of being displayed and then ignored.
        """
        if location.strip() or latitude is not None or longitude is not None:
            label, lat, lon = await self._resolve(location, latitude, longitude)
        else:
            label, lat, lon = (
                self.config.location,
                self.config.latitude,
                self.config.longitude,
            )
        report = await self._fetch(lat, lon, label)
        advisories = self.evaluate(report)
        message = self.render(report, advisories)
        fingerprint = _fingerprint(report, advisories)
        return WeatherCheck(
            report=report,
            advisories=tuple(advisories),
            message=message,
            fingerprint=fingerprint,
        )

    async def _resolve(
        self,
        location: str,
        latitude: float | None,
        longitude: float | None,
    ) -> tuple[str, float, float]:
        """Coordinates for a per-call place, geocoding a name when needed."""
        wanted = location.strip()
        if latitude is not None and longitude is not None:
            return (wanted or f"{latitude:.4f},{longitude:.4f}", latitude, longitude)
        if not wanted:
            raise WeatherError("a location name or both latitude and longitude are required")
        found = await self.geocode(wanted)
        return found

    async def geocode(self, name: str) -> tuple[str, float, float]:
        """Look a place name up with Open-Meteo's keyless geocoder.

        Same provider as the forecast, so one outage does not split the feature
        across two vendors. The returned label is the matched place name, which
        is what the owner should see in the report.
        """
        params = {"name": name, "count": 1, "language": "zh", "format": "json"}
        try:
            response = await self.http.get(
                GEOCODE_URL,
                params=params,
                timeout=self.config.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise WeatherError(
                f"geocoding {name!r} failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise WeatherError(
                f"geocoding {name!r} returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeatherError("geocoding response was not JSON") from exc
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            raise WeatherError(f"no place called {name!r} was found")
        first = results[0]
        if not isinstance(first, dict):
            raise WeatherError(f"no place called {name!r} was found")
        try:
            latitude = float(first["latitude"])
            longitude = float(first["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherError(f"geocoding {name!r} returned no coordinates") from exc
        label = str(first.get("name") or name)
        admin = str(first.get("admin1") or "")
        if admin and admin not in label:
            label = f"{label}（{admin}）"
        return label, latitude, longitude

    # -- data ------------------------------------------------------------

    async def _fetch(self, latitude: float, longitude: float, location: str) -> WeatherReport:
        now = datetime.now(self.timezone)
        key = forecast_cache_key(latitude, longitude, location, now)
        if self.cache is not None:
            cached = self.cache.get(key, now=now)
            if cached is not None:
                logger.debug("weather cache hit for %s", key)
                return cached
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_sum,precipitation_probability_max,"
                "snowfall_sum,wind_speed_10m_max"
            ),
            "past_days": 1,
            "forecast_days": 2,
            "timezone": self.timezone.key,
        }
        try:
            response = await self.http.get(
                FORECAST_URL,
                params=params,
                timeout=self.config.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise WeatherError(
                f"weather request failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise WeatherError(f"weather request returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeatherError("weather response was not JSON") from exc
        report = parse_open_meteo(payload, location, self.timezone)
        if self.cache is not None:
            self.cache.put(key, report, now=now)
        return report

    # -- rules -----------------------------------------------------------

    def evaluate(self, report: WeatherReport) -> list[Advisory]:
        today = report.today
        tomorrow = report.tomorrow
        delta = round(tomorrow.temperature_max - today.temperature_max, 1)
        advisories: list[Advisory] = []

        if _is_snow(today):
            advisories.append(
                Advisory("snow", "今天有雪，路面湿滑，注意保暖防滑。", "warning")
            )
        elif _is_wet(today, self.config):
            probability = today.precipitation_probability
            advisories.append(
                Advisory(
                    "rain",
                    f"今天有雨（降水概率 {probability}%），出门记得带伞。",
                )
            )

        if _is_snow(tomorrow):
            advisories.append(
                Advisory("snow_tomorrow", "明天有雪，注意保暖防滑。", "warning")
            )
        elif _is_wet(tomorrow, self.config) and not _is_wet(today, self.config):
            advisories.append(
                Advisory(
                    "rain_tomorrow",
                    f"明天有雨（降水概率 {tomorrow.precipitation_probability}%），"
                    "出门记得带伞。",
                )
            )

        if delta <= -self.config.temperature_delta:
            advisories.append(
                Advisory(
                    "temperature_drop",
                    f"明天最高温比今天低 {abs(delta):g}°C"
                    f"（{_temperature(today.temperature_max)}→"
                    f"{_temperature(tomorrow.temperature_max)}），记得加衣。",
                )
            )
        elif delta >= self.config.temperature_delta:
            advisories.append(
                Advisory(
                    "temperature_rise",
                    f"明天最高温比今天高 {delta:g}°C"
                    f"（{_temperature(today.temperature_max)}→"
                    f"{_temperature(tomorrow.temperature_max)}），注意增减衣物。",
                )
            )

        if today.temperature_max >= self.config.heat_threshold:
            advisories.append(
                Advisory(
                    "heat",
                    f"今天最高温 {_temperature(today.temperature_max)}°C，注意防暑补水。",
                )
            )
        if today.temperature_min <= self.config.cold_threshold:
            advisories.append(
                Advisory(
                    "cold",
                    f"今天最低温 {_temperature(today.temperature_min)}°C，注意保暖。",
                )
            )
        if max(today.wind_max, tomorrow.wind_max) >= self.config.wind_threshold:
            advisories.append(
                Advisory(
                    "wind",
                    f"风力较大（明天最高 {tomorrow.wind_max:g} km/h），"
                    "注意高空坠物。",
                    "warning",
                )
            )
        if today.code in SEVERE_CODES or tomorrow.code in SEVERE_CODES:
            advisories.append(
                Advisory(
                    "severe",
                    "有强降雨、强降雪或雷暴，尽量减少外出并留意官方预警。",
                    "warning",
                )
            )
        return advisories

    def render(
        self, report: WeatherReport, advisories: list[Advisory] | tuple[Advisory, ...]
    ) -> str:
        today = report.today
        tomorrow = report.tomorrow
        fetched = report.fetched_at.astimezone(self.timezone).strftime("%m-%d %H:%M")
        lines = [
            f"{report.location} · {today.date}（数据：{SOURCE_NAME}，{fetched}）",
            "",
            f"今天 {today.description}，"
            f"{_temperature(today.temperature_min)}~"
            f"{_temperature(today.temperature_max)}°C，"
            f"降水概率 {today.precipitation_probability}%"
            f"（{today.precipitation:g} mm）",
            f"明天 {tomorrow.description}，"
            f"{_temperature(tomorrow.temperature_min)}~"
            f"{_temperature(tomorrow.temperature_max)}°C，"
            f"降水概率 {tomorrow.precipitation_probability}%"
            f"（{tomorrow.precipitation:g} mm）",
        ]
        if advisories:
            lines.append("")
            lines.append("提醒：")
            lines.extend(f"- {advisory.text}" for advisory in advisories)
        else:
            lines.extend(
                ["", "今明两天没有需要特别提醒的天气，按平常安排即可。"]
            )
        return "\n".join(lines)


def parse_open_meteo(
    payload: dict[str, Any], location: str, timezone: ZoneInfo
) -> WeatherReport:
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise WeatherError("weather response has no daily block")
    dates = daily.get("time")
    if not isinstance(dates, list) or len(dates) < 2:
        raise WeatherError("weather response has fewer than two days")

    days: list[ForecastDay] = []
    for index, day in enumerate(dates):
        days.append(
            ForecastDay(
                date=str(day),
                code=_number(daily, "weather_code", index, 0),
                temperature_max=_number(daily, "temperature_2m_max", index),
                temperature_min=_number(daily, "temperature_2m_min", index),
                precipitation=_number(daily, "precipitation_sum", index),
                precipitation_probability=int(
                    _number(daily, "precipitation_probability_max", index, 0)
                ),
                snowfall=_number(daily, "snowfall_sum", index, 0),
                wind_max=_number(daily, "wind_speed_10m_max", index, 0),
            )
        )
    # with past_days=1 and forecast_days=2 the array is yesterday, today,
    # tomorrow; a provider that returns only two days still ends at tomorrow.
    if len(days) >= 3:
        yesterday, today, tomorrow = days[-3], days[-2], days[-1]
    else:
        yesterday, today, tomorrow = None, days[-2], days[-1]

    return WeatherReport(
        location=location,
        fetched_at=datetime.now(timezone),
        yesterday=yesterday,
        today=today,
        tomorrow=tomorrow,
    )


def _number(
    daily: dict[str, Any], key: str, index: int, default: float | None = None
) -> float:
    values = daily.get(key)
    if not isinstance(values, list) or index >= len(values):
        if default is None:
            raise WeatherError(f"weather response is missing {key}")
        return float(default)
    value = values[index]
    if value is None:
        if default is None:
            raise WeatherError(f"weather response has no value for {key}")
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise WeatherError(f"weather value for {key} is not numeric") from exc


def _is_wet(day: ForecastDay, config: WeatherConfig) -> bool:
    return (
        day.code in RAIN_CODES
        or day.code in SNOW_CODES
        or day.precipitation_probability >= config.rain_probability
        or day.precipitation >= 0.5
    )


def _is_snow(day: ForecastDay) -> bool:
    return day.code in SNOW_CODES or day.snowfall > 0


def _temperature(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _fingerprint(report: WeatherReport, advisories: list[Advisory]) -> str:
    kinds = ",".join(sorted({advisory.kind for advisory in advisories})) or "none"
    return f"{report.today.date}:{kinds}"


@dataclass(frozen=True)
class WeatherRun:
    status: str
    checked_at: datetime
    message: str = ""
    conversation_id: str = ""
    advisories: tuple[str, ...] = ()
    error: str = ""


class WeatherWatch:
    """Run a check on schedule and record a notice when something changed."""

    def __init__(
        self,
        *,
        config: WeatherConfig,
        service: WeatherService,
        store: ConversationStore,
        registry: Any,
        timezone: ZoneInfo,
        state_path: Path,
        http: httpx.AsyncClient | None = None,
        clock=None,
        tick_seconds: float = 30.0,
        phone: PhoneGateway | None = None,
    ) -> None:
        self.config = config
        self.service = service
        self.store = store
        self.registry = registry
        self.timezone = timezone
        self.state_path = state_path
        self.http = http
        self.phone = phone
        self.clock = clock or (lambda: datetime.now(timezone))
        self.tick_seconds = tick_seconds
        self._schedule: Schedule = parse_schedule(config.schedule)
        self._state: dict[str, Any] = self._load()
        self._loop: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self._state.get("next_run"):
            self._state["next_run"] = _iso(self._schedule.next_after(self.clock()))
            self._save()
        self._loop = asyncio.create_task(self._run_loop(), name="weather-watch")

    async def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()
            try:
                await self._loop
            except asyncio.CancelledError:
                pass
            self._loop = None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "location": self.config.location,
            "follow_phone": self.config.follow_phone,
            "last_location": self._state.get("last_location"),
            "schedule": self.config.schedule,
            "next_run": self._state.get("next_run"),
            "last_run": self._state.get("last_run"),
            "last_status": self._state.get("last_status"),
            "last_conversation_id": self._state.get("last_conversation_id"),
            "webhook": bool(self.config.webhook),
        }

    async def run_now(self, *, force: bool = True) -> WeatherRun:
        # A manual check is a diagnostic action; it must not move the daily
        # schedule, or testing in the morning would skip that morning's run.
        return await self._check(self.clock(), force=force, manual=True)

    async def tick(self, now: datetime | None = None) -> WeatherRun | None:
        moment = now or self.clock()
        next_run = _parse_iso(self._state.get("next_run"))
        if next_run is None:
            self._state["next_run"] = _iso(self._schedule.next_after(moment))
            self._save()
            return None
        if next_run > moment:
            return None
        return await self._check(moment, force=False, manual=False)

    # -- internals -------------------------------------------------------

    async def _check(
        self, moment: datetime, *, force: bool, manual: bool
    ) -> WeatherRun:
        if not self.config.enabled:
            return self._finish(
                WeatherRun(
                    status="disabled",
                    checked_at=moment,
                    error="weather is disabled in the configuration",
                ),
                moment,
                advance=not manual,
            )
        if not manual and not self._skill_enabled():
            return self._finish(
                WeatherRun(
                    status="disabled",
                    checked_at=moment,
                    error="the weather skill is disabled",
                ),
                moment,
                advance=True,
            )
        place = await self._place(moment)
        try:
            check = await self.service.check(**place)
        except WeatherError as exc:
            logger.warning("weather check failed: %s", exc)
            return self._finish(
                WeatherRun(status="error", checked_at=moment, error=str(exc)),
                moment,
                advance=not manual,
            )

        if not check.advisories:
            run = WeatherRun(
                status="no_advisory",
                checked_at=moment,
                message=check.message,
            )
            self._state["last_fingerprint"] = check.fingerprint
            return self._finish(run, moment, advance=not manual)

        if (
            not force
            and check.fingerprint == self._state.get("last_fingerprint")
        ):
            run = WeatherRun(
                status="duplicate",
                checked_at=moment,
                message=check.message,
                advisories=tuple(a.kind for a in check.advisories),
            )
            return self._finish(run, moment, advance=not manual)

        self._state["last_location"] = check.report.location
        conversation = await self.store.create_notice(
            title=f"天气提醒 · {check.report.location}",
            text=check.message,
            source="weather",
            channel="weather",
            now=moment,
        )
        delivery_error = await self._deliver(check, conversation.id)
        if delivery_error:
            logger.warning("weather webhook failed: %s", delivery_error)
        self._state["last_fingerprint"] = check.fingerprint
        self._state["last_conversation_id"] = conversation.id
        run = WeatherRun(
            status="ok",
            checked_at=moment,
            message=check.message,
            conversation_id=conversation.id,
            advisories=tuple(a.kind for a in check.advisories),
            error=delivery_error,
        )
        return self._finish(run, moment, advance=not manual)

    async def _place(self, moment: datetime) -> dict[str, Any]:
        """Coordinates for this check, per the owner's stored rule.

        The rule is "use the coordinates when they are available, the configured
        place otherwise". A fix older than ``phone_fix_max_age_minutes`` is not
        available: reporting another city from last night's coordinates is worse
        than the configured default. Any failure here falls back the same way,
        because a missing location must not cost the owner the notice.
        """
        if not self.config.follow_phone or self.phone is None:
            return {}
        window = timedelta(minutes=self.config.phone_fix_max_age_minutes)
        try:
            fixes = await self.phone.locations(since=moment - window, limit=1)
        except Exception:  # noqa: BLE001 - fall back to the configured place
            logger.warning("weather: phone location lookup failed", exc_info=True)
            return {}
        if not fixes:
            return {}
        fix = fixes[0]
        try:
            latitude = float(fix["latitude"])
            longitude = float(fix["longitude"])
        except (KeyError, TypeError, ValueError):
            return {}
        if latitude == 0.0 and longitude == 0.0:
            return {}
        return {
            "location": _place_label(fix),
            "latitude": latitude,
            "longitude": longitude,
        }

    def _finish(
        self, run: WeatherRun, moment: datetime, *, advance: bool = True
    ) -> WeatherRun:
        self._state["last_run"] = _iso(moment)
        self._state["last_status"] = run.status
        if advance:
            self._state["next_run"] = _iso(self._schedule.next_after(moment))
        if run.error and run.status != "ok":
            self._state["last_error"] = run.error
        else:
            self._state.pop("last_error", None)
        self._save()
        return run

    def _skill_enabled(self) -> bool:
        try:
            views = self.registry.views()
        except Exception:  # noqa: BLE001 - a missing registry must not stop the loop
            return False
        return any(
            view.name == "weather" and view.enabled and view.available
            for view in views
        )

    async def _deliver(self, check: WeatherCheck, conversation_id: str) -> str:
        if not self.config.webhook:
            return ""
        if self.http is None:
            return "no HTTP client for webhook delivery"
        try:
            response = await self.http.post(
                self.config.webhook,
                json={
                    "kind": "weather",
                    "location": check.report.location,
                    "title": f"天气提醒 · {check.report.location}",
                    "text": check.message,
                    "conversation_id": conversation_id,
                },
                timeout=self.config.webhook_timeout_seconds,
            )
            if response.status_code >= 400:
                return f"webhook returned HTTP {response.status_code}"
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return f"webhook failed: {type(exc).__name__}"
        return ""

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive one bad tick
                logger.exception("weather watch tick failed")
            await asyncio.sleep(self.tick_seconds)

    def _load(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("weather state is unreadable; starting fresh")
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        atomic_write(
            self.state_path,
            json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )


def _place_label(fix: dict[str, Any]) -> str:
    """A short readable name for a location row: city + district, else address."""
    city = str(fix.get("city") or "").strip()
    district = str(fix.get("district") or "").strip()
    if city and district and not city.endswith(district):
        return f"{city}{district}"
    if city:
        return city
    address = str(fix.get("address") or "").strip()
    if address:
        return address
    try:
        return f"{float(fix['latitude']):.4f},{float(fix['longitude']):.4f}"
    except (KeyError, TypeError, ValueError):
        return ""


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
