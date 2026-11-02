#!/usr/bin/env python3
"""Cache-aware weather lookups for agent sessions.

Why this exists: a model's context is discarded when a conversation ends, so a
forecast fetched in one session is invisible in the next one. This helper keeps
the payload on disk with a TTL, so a repeated question inside the TTL is
answered without a network call and the answer can state how old the data is.

The cache lives at ``.cache/weather/cache.json`` (gitignored). Entries are
keyed by the exact question (rounded coordinates, forecast window, local date,
timezone), never by the raw question text, so two phrasings of the same place
and window share one entry.

Usage::

    python3 scripts/weather-cache.py --place 武汉
    python3 scripts/weather-cache.py --place 武汉 --days 7
    python3 scripts/weather-cache.py --lat 30.59 --lon 114.31 --days 3
    python3 scripts/weather-cache.py --place 上海 --hourly
    python3 scripts/weather-cache.py --force --place 武汉   # bypass the cache
    python3 scripts/weather-cache.py --status               # what is cached
    python3 scripts/weather-cache.py --purge                # drop expired entries

Output is one JSON object on stdout, so the caller can quote the ``note`` field
("cached at 12:03, 8 min old, TTL 30 min") instead of guessing. A stale entry
is served only when the live request fails, and then it is flagged
``"stale": true``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

CACHE_VERSION = 1
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "weather"
CACHE_FILE = CACHE_DIR / "cache.json"

# Geocoding results change on the scale of weeks, not hours.
GEOCODE_TTL = 7 * 24 * 3600
# (maximum forecast_days, TTL in seconds). A one-day window is "today" and is
# what people come back to within the hour; the far window is model output that
# Open-Meteo itself refreshes a few times a day, so caching it longer loses
# nothing.
FORECAST_TTLS = ((1, 30 * 60), (3, 3 * 3600), (7, 6 * 3600), (16, 12 * 3600))
MAX_ENTRIES = 200
TIMEOUT = 20
USER_AGENT = "winnie-agent-weather-cache/1.0 (local cache helper)"

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

CURRENT_FIELDS = ",".join(
    [
        "temperature_2m",
        "apparent_temperature",
        "relative_humidity_2m",
        "precipitation",
        "weather_code",
        "wind_speed_10m",
    ]
)
DAILY_FIELDS = ",".join(
    [
        "weather_code",
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_probability_max",
        "precipitation_sum",
        "wind_speed_10m_max",
        "sunrise",
        "sunset",
    ]
)
HOURLY_FIELDS = ",".join(
    ["temperature_2m", "precipitation_probability", "weather_code"]
)


# ---------------------------------------------------------------- storage


def now_epoch() -> float:
    return time.time()


def load_cache() -> dict:
    """Return the cache document, or a fresh one for a missing/corrupt file."""
    try:
        raw = CACHE_FILE.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, ValueError):
        return {"version": CACHE_VERSION, "entries": {}}
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), dict):
        return {"version": CACHE_VERSION, "entries": {}}
    return doc


def save_cache(doc: dict) -> None:
    """Write atomically, so a killed process cannot leave a half-written file."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(CACHE_DIR), prefix=".cache-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, CACHE_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def prune(doc: dict, ttl_of=lambda entry: entry.get("ttl_seconds", 0)) -> int:
    """Drop expired entries and cap the file; return how many were removed."""
    now = now_epoch()
    entries = doc["entries"]
    expired = [
        key
        for key, entry in entries.items()
        if now - float(entry.get("fetched_at", 0)) > float(ttl_of(entry))
    ]
    for key in expired:
        del entries[key]
    if len(entries) > MAX_ENTRIES:
        ordered = sorted(entries.items(), key=lambda item: item[1].get("fetched_at", 0))
        for key, _ in ordered[: len(entries) - MAX_ENTRIES]:
            del entries[key]
    return len(expired)


def read_entry(key: str) -> dict | None:
    entry = load_cache()["entries"].get(key)
    return entry if isinstance(entry, dict) else None


def entry_is_fresh(entry: dict) -> bool:
    age = now_epoch() - float(entry.get("fetched_at", 0))
    return age <= float(entry.get("ttl_seconds", 0))


def cache_meta(key: str, entry: dict, hit: bool, stale: bool) -> dict:
    fetched = datetime.fromtimestamp(float(entry["fetched_at"]), tz=timezone.utc)
    local = fetched.astimezone(ZoneInfo(entry.get("timezone") or "Asia/Shanghai"))
    age = int(now_epoch() - float(entry["fetched_at"]))
    ttl = int(entry.get("ttl_seconds", 0))
    left = max(0, ttl - age)
    note = (
        f"cached at {local:%H:%M} ({local:%Y-%m-%d %Z}), {human(age)} old, "
        f"TTL {human(ttl)}"
    )
    if stale:
        note += ", STALE: the live request failed, re-check before relying on it"
    elif hit:
        note += f", {human(left)} left"
    return {
        "hit": hit,
        "stale": stale,
        "key": key,
        "fetched_at": local.isoformat(),
        "age_seconds": age,
        "ttl_seconds": ttl,
        "expires_in": left,
        "note": note,
    }


def human(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{round(seconds / 60)}min"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.0f}d"


# ---------------------------------------------------------------- network


def http_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise LookupError(f"HTTP {exc.code} from {url}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise LookupError(f"cannot reach {url}: {exc}") from exc


def key_for_forecast(lat: float, lon: float, days: int, tz: str, hourly: bool) -> str:
    # hourly=1 is part of the key: a lean daily payload must never satisfy a
    # request that also asked for the hourly series, and vice versa (the first
    # version shared one key and silently served the wrong shape).
    today = datetime.now(ZoneInfo(tz)).date().isoformat()
    shape = "hourly" if hourly else "daily"
    return f"forecast|open-meteo|{lat:.2f}|{lon:.2f}|days={days}|tz={tz}|{shape}|from={today}"


def key_for_geocode(place: str, language: str) -> str:
    return f"geocode|open-meteo|{language}|{place.strip().casefold()}"


def forecast_url(lat: float, lon: float, days: int, tz: str, hourly: bool) -> str:
    query = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "timezone": tz,
        "forecast_days": str(days),
        "current": CURRENT_FIELDS,
        "daily": DAILY_FIELDS,
    }
    if hourly:
        query["hourly"] = HOURLY_FIELDS
    return FORECAST_URL + "?" + urllib.parse.urlencode(query)


def geocode_url(place: str, language: str) -> str:
    query = {"name": place, "count": "5", "language": language, "format": "json"}
    return GEOCODE_URL + "?" + urllib.parse.urlencode(query)


def ttl_for_days(days: int) -> int:
    for limit, ttl in FORECAST_TTLS:
        if days <= limit:
            return ttl
    return FORECAST_TTLS[-1][1]


# ---------------------------------------------------------------- commands


def resolve_place(place: str, language: str, force: bool) -> tuple[dict, dict]:
    """Return (geocode result, cache meta) for a place name."""
    key = key_for_geocode(place, language)
    entry = None if force else read_entry(key)
    if entry and entry_is_fresh(entry):
        return entry["payload"], cache_meta(key, entry, hit=True, stale=False)
    stale_entry = entry
    try:
        payload = http_json(geocode_url(place, language))
    except LookupError as exc:
        if stale_entry:
            return stale_entry["payload"], cache_meta(key, stale_entry, hit=False, stale=True)
        raise
    results = payload.get("results") or []
    if not results:
        raise LookupError(f"no place called {place!r} in Open-Meteo geocoding")
    best = results[0]
    entry = {
        "fetched_at": now_epoch(),
        "ttl_seconds": GEOCODE_TTL,
        "timezone": best.get("timezone") or "Asia/Shanghai",
        "url": geocode_url(place, language),
        "payload": best,
    }
    doc = load_cache()
    doc["entries"][key] = entry
    prune(doc)
    save_cache(doc)
    return best, cache_meta(key, entry, hit=False, stale=False)


def lookup(args: argparse.Namespace) -> dict:
    place_meta = None
    if args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
        place = None
    else:
        place, place_meta = resolve_place(args.place, args.language, args.force)
        lat, lon = float(place["latitude"]), float(place["longitude"])
        lat, lon = round(lat, 2), round(lon, 2)

    tz = args.timezone or (place or {}).get("timezone") or "Asia/Shanghai"
    days = args.days
    url = forecast_url(lat, lon, days, tz, args.hourly)
    key = key_for_forecast(lat, lon, days, tz, bool(args.hourly))

    entry = None if args.force else read_entry(key)
    if entry and entry_is_fresh(entry):
        meta = cache_meta(key, entry, hit=True, stale=False)
        data = entry["payload"]
    else:
        stale_entry = entry
        meta = None
        try:
            data = http_json(url)
        except LookupError:
            if stale_entry is None:
                raise
            meta = cache_meta(key, stale_entry, hit=False, stale=True)
            data = stale_entry["payload"]
        if meta is None:
            entry = {
                "fetched_at": now_epoch(),
                "ttl_seconds": ttl_for_days(days),
                "timezone": tz,
                "url": url,
                "payload": data,
            }
            doc = load_cache()
            doc["entries"][key] = entry
            prune(doc)
            save_cache(doc)
            meta = cache_meta(key, entry, hit=False, stale=False)

    result = {
        "ok": True,
        "source": "open-meteo",
        "cache": meta,
        "request": {
            "latitude": lat,
            "longitude": lon,
            "days": days,
            "timezone": tz,
            "hourly": bool(args.hourly),
            "url": url,
        },
        "forecast": data,
    }
    if place_meta is not None:
        result["place"] = place
        result["place_cache"] = place_meta
    return result


def status() -> dict:
    doc = load_cache()
    now = now_epoch()
    rows = []
    for key, entry in sorted(doc["entries"].items(), key=lambda kv: -kv[1].get("fetched_at", 0)):
        age = int(now - float(entry.get("fetched_at", 0)))
        ttl = int(entry.get("ttl_seconds", 0))
        rows.append(
            {
                "key": key,
                "age": human(age),
                "ttl": human(ttl),
                "fresh": age <= ttl,
                "url": entry.get("url", ""),
            }
        )
    return {"ok": True, "file": str(CACHE_FILE), "entries": rows, "count": len(rows)}


def purge() -> dict:
    doc = load_cache()
    removed = prune(doc)
    save_cache(doc)
    return {"ok": True, "removed": removed, "remaining": len(doc["entries"])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cache-aware weather lookup (Open-Meteo), shared across sessions."
    )
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--place", help="city or district name, e.g. 武汉")
    where.add_argument("--lat", type=float, help="latitude, paired with --lon")
    parser.add_argument("--lon", type=float, help="longitude, paired with --lat")
    parser.add_argument("--days", type=int, default=1, choices=range(1, 17),
                        help="forecast days, 1-16 (default 1)")
    parser.add_argument("--timezone", help="IANA timezone, default: from the place")
    parser.add_argument("--language", default="zh", help="geocoding language, default zh")
    parser.add_argument("--hourly", action="store_true", help="include hourly series")
    parser.add_argument("--force", action="store_true", help="ignore the cache, refetch")
    parser.add_argument("--status", action="store_true", help="list cache entries")
    parser.add_argument("--purge", action="store_true", help="drop expired entries")
    args = parser.parse_args(argv)

    if args.lat is not None and args.lon is None:
        parser.error("--lat needs --lon")
    if args.lon is not None and args.lat is None:
        parser.error("--lon needs --lat")
    if not (args.status or args.purge or args.place or args.lat is not None):
        parser.error("give --place, --lat/--lon, --status or --purge")

    try:
        if args.status:
            result = status()
        elif args.purge:
            result = purge()
        else:
            result = lookup(args)
    except LookupError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        print(f"weather-cache: {exc}", file=sys.stderr)
        return 2

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
