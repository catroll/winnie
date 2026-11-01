"""Reverse geocoding: provider parsing, caching, the sync path and backfill.

Both providers are exercised through an ``httpx.MockTransport`` so the assertions
are about our parsing of their shapes (Amap's empty ``city`` and ``[]`` for a
missing string, Photon's optional ``state``/``district``/``name``) rather than
about the network. The service tests use a fake geocoder so a failure can be
injected where it matters.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError

from winnie_api.main import create_app
from winnie_api.settings import DatabaseConfig, Settings
from winnie_core.db import create_all, create_engine, create_session_factory
from winnie_phone.config import PhoneConfig, PhoneGeocodeConfig
from winnie_phone.geocode import (
    Address,
    AmapGeocoder,
    NullGeocoder,
    PhotonGeocoder,
    build_geocoder,
    parse_amap,
    parse_photon,
    wgs84_to_gcj02,
)
from winnie_phone.schemas import DeviceRegister, SyncRequest
from winnie_phone.service import PhoneService

NOW = datetime.now(timezone.utc)
KEY_ENV = "TEST_AMAP_KEY"

AMAP_OK = {
    "status": "1",
    "info": "OK",
    "regeocode": {
        "formatted_address": "北京市朝阳区望京街道阜通东大街6号",
        "addressComponent": {
            "province": "北京市",
            # A municipality reports the city as an empty list, not a string.
            "city": [],
            "district": "朝阳区",
            "township": "望京街道",
            "streetNumber": {"street": "阜通东大街", "number": "6号"},
        },
        "pois": [{"name": "望京SOHO", "distance": "12"}],
    },
}

# Captured from photon.komoot.io/reverse. A municipality has no ``state``, a
# county reports ``county`` instead of ``district``, and a rural road has no
# ``name`` — the three shapes the parser has to survive.
PHOTON_BEIJING = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "osm_type": "W",
                "osm_key": "building",
                "osm_value": "yes",
                "type": "house",
                "name": "11号楼",
                "street": "阜安东路",
                "locality": "小望京",
                "district": "望京街道",
                "city": "北京市",
                "country": "中国",
                "postcode": "100102",
                "countrycode": "CN",
            },
            "geometry": {"type": "Point", "coordinates": [116.4796939, 39.9955736]},
        }
    ],
}

PHOTON_WUHAN = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "type": "house",
                "name": "横渡长江博物馆",
                "street": "汉口江滩绿道",
                "locality": "三阳社区",
                "district": "一元街道",
                "city": "武汉市",
                "state": "湖北省",
                "country": "中国",
                "countrycode": "CN",
            },
            "geometry": {"type": "Point", "coordinates": [114.3020873, 30.5931567]},
        }
    ],
}

PHOTON_INDIA = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "type": "street",
                "name": "Becharam Chatterjee Road",
                "county": "Thakurpukur Maheshtala",
                "state": "West Bengal",
                "country": "India",
                "countrycode": "IN",
            },
            "geometry": {"type": "Point", "coordinates": [88.2994079, 22.4990374]},
        }
    ],
}


class FakeAmap:
    """Serve the regeo endpoint and count calls per coordinate."""

    def __init__(self, body: dict | None = None, status: int = 200) -> None:
        self.calls: list[tuple[str, str]] = []
        self.body = body if body is not None else AMAP_OK
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.params.get("location", ""), request.url.params.get("key", "")))
        return httpx.Response(self.status, json=self.body)


class FakePhoton:
    """Serve the Photon endpoint, keeping the raw params and headers."""

    def __init__(self, body: dict | None = None, status: int = 200) -> None:
        self.calls: list[dict] = []
        self.headers: list[httpx.Headers] = []
        self.body = body if body is not None else PHOTON_WUHAN
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(dict(request.url.params))
        self.headers.append(request.headers)
        return httpx.Response(self.status, json=self.body)


class FakeGeocoder:
    name = "fake"

    def __init__(self, *, fail: bool = False, name: str | None = None) -> None:
        self.calls: list[tuple[float, float]] = []
        self.fail = fail
        if name is not None:
            self.name = name

    async def reverse(self, latitude: float, longitude: float) -> Address | None:
        self.calls.append((latitude, longitude))
        if self.fail:
            raise RuntimeError("provider down")
        return Address(
            formatted=f"测试地址 {latitude:.3f},{longitude:.3f}",
            province="北京市",
            city="北京市",
            district="朝阳区",
            township="望京街道",
            street="阜通东大街",
            street_number="6号",
            poi="望京SOHO (12m)",
            provider=self.name,
        )

    async def aclose(self) -> None:
        return None


def fix(external_id: str, latitude: float = 39.996, longitude: float = 116.48, **overrides) -> dict:
    return {
        "external_id": external_id,
        "latitude": latitude,
        "longitude": longitude,
        "accuracy_m": 12.0,
        "provider": "gps",
        "captured_at": (NOW - timedelta(minutes=5)).isoformat(),
        **overrides,
    }


def geocode_config(**overrides) -> PhoneGeocodeConfig:
    settings = {
        "enabled": True,
        "key_env": KEY_ENV,
        "min_interval_seconds": 0.0,
        "timeout_seconds": 2.0,
        "cache_entries": 8,
        "max_per_sync": 5,
    }
    settings.update(overrides)
    return PhoneGeocodeConfig(**settings)


async def service_for(tmp_path: Path, config: PhoneConfig, geocoder=None):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'geocode.db'}")
    await create_all(engine)
    session_factory = create_session_factory(engine)
    service = PhoneService(config, session_factory=session_factory, geocoder=geocoder)
    device = await service.register_device(
        "local", DeviceRegister(device_key="geocode-device-1", name="Pixel")
    )
    return engine, service, device


# --------------------------------------------------------------------------- #
# Provider parsing
# --------------------------------------------------------------------------- #


def test_parse_amap_composes_a_municipality_address() -> None:
    address = parse_amap(AMAP_OK)
    assert address is not None
    assert address.province == "北京市"
    # An empty city is filled from the province, so "省 + 市 + 区" never has a hole.
    assert address.city == "北京市"
    assert address.district == "朝阳区"
    assert address.township == "望京街道"
    assert address.street == "阜通东大街"
    assert address.street_number == "6号"
    assert address.poi == "望京SOHO (12m)"
    assert address.one_line() == "北京市 朝阳区 望京SOHO (12m)"
    payload = address.to_payload()
    assert payload["formatted"] == "北京市朝阳区望京街道阜通东大街6号"
    assert payload["provider"] == "amap"
    assert payload["at"]


def test_parse_amap_falls_back_when_fields_are_missing() -> None:
    body = {
        "status": "1",
        "regeocode": {
            "formatted_address": [],
            "addressComponent": {"province": "湖北省", "city": "武汉市", "district": []},
        },
    }
    address = parse_amap(body)
    assert address is not None
    assert address.formatted == "湖北省 武汉市"
    assert address.district == ""
    assert address.poi == ""


@pytest.mark.parametrize(
    "body",
    [
        {"status": "0", "info": "INVALID_USER_SCODE"},
        {"status": "1", "regeocode": {}},
        {"status": "1", "regeocode": {"addressComponent": {}}},
        "not-json",
        None,
    ],
)
def test_parse_amap_rejects_anything_unusable(body) -> None:
    assert parse_amap(body) is None


def test_parse_photon_fills_a_municipality_from_its_city() -> None:
    address = parse_photon(PHOTON_BEIJING)
    assert address is not None
    # No ``state`` in the body: the city is the province, as in Amap's shape.
    assert address.province == "北京市"
    assert address.city == "北京市"
    assert address.district == "望京街道"
    assert address.township == "小望京"
    assert address.street == "阜安东路"
    assert address.poi == "11号楼"
    # The city is not repeated, so the line does not read "北京市 北京市".
    assert address.formatted == "北京市 望京街道 小望京 阜安东路 11号楼"
    assert address.one_line() == "北京市 望京街道 11号楼"
    assert address.to_payload()["provider"] == "photon"


def test_parse_photon_reads_a_province_and_a_named_place() -> None:
    address = parse_photon(PHOTON_WUHAN)
    assert address is not None
    assert address.province == "湖北省"
    assert address.city == "武汉市"
    assert address.district == "一元街道"
    assert address.township == "三阳社区"
    assert address.poi == "横渡长江博物馆"


def test_parse_photon_uses_county_when_there_is_no_district() -> None:
    address = parse_photon(PHOTON_INDIA)
    assert address is not None
    assert address.province == "West Bengal"
    assert address.city == ""
    assert address.district == "Thakurpukur Maheshtala"
    # The rural road is the feature itself, so its name is the place.
    assert address.poi == "Becharam Chatterjee Road"
    assert address.formatted == (
        "West Bengal Thakurpukur Maheshtala Becharam Chatterjee Road"
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"features": []},
        {"features": [{}]},
        {"features": [{"properties": {}}]},
        {"features": "not-a-list"},
        "not-json",
        None,
    ],
)
def test_parse_photon_rejects_anything_unusable(body) -> None:
    assert parse_photon(body) is None


def test_parse_photon_ignores_a_feature_far_from_the_fix() -> None:
    # The same body 20 km from the fix: OSM's nearest named place is no longer a
    # description of where the owner was, so the fix keeps its coordinates.
    assert parse_photon(PHOTON_WUHAN, near=(30.77, 114.30)) is None
    assert parse_photon(PHOTON_WUHAN, near=(30.593, 114.302)) is not None


# --------------------------------------------------------------------------- #
# The datum shift
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("wgs_lat", "wgs_lon", "gcj_lat", "gcj_lon"),
    [
        # Reference values from gcoord 1.0.7 (unpkg.com/gcoord@1.0.7), an
        # independent implementation of the same published transform. Editing the
        # constants below without re-deriving these makes the test fail, which is
        # the point: a wrong datum shift is a valid coordinate on the wrong street.
        (39.908692, 116.397477, 39.910095, 116.403721),
        (39.996, 116.48, 39.997284, 116.486093),
        (30.437588, 114.434242, 30.435241, 114.439735),
    ],
)
def test_wgs84_to_gcj02_matches_a_reference_implementation(
    wgs_lat, wgs_lon, gcj_lat, gcj_lon
) -> None:
    latitude, longitude = wgs84_to_gcj02(wgs_lat, wgs_lon)
    assert latitude == pytest.approx(gcj_lat, abs=1e-6)
    assert longitude == pytest.approx(gcj_lon, abs=1e-6)


def test_wgs84_to_gcj02_leaves_points_outside_mainland_china_alone() -> None:
    # GCJ-02 is defined only inside China; shifting a fix abroad would be a wrong
    # answer invented by us, and Amap has no use for it either.
    assert wgs84_to_gcj02(48.8566, 2.3522) == (48.8566, 2.3522)


# --------------------------------------------------------------------------- #
# The provider client
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_amap_client_queries_lon_lat_and_caches_nearby_fixes() -> None:
    fake = FakeAmap()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        geocoder = AmapGeocoder(geocode_config(), "secret-key", http)
        first = await geocoder.reverse(39.9961, 116.4801)
        # ~30 m away: the same cache entry, so the provider is not asked again.
        second = await geocoder.reverse(39.9962, 116.4802)
    assert first is not None and second is not None
    assert len(fake.calls) == 1
    location, key = fake.calls[0]
    assert key == "secret-key"
    # Amap wants longitude first, and wants GCJ-02 rather than the WGS-84 fix.
    latitude, longitude = wgs84_to_gcj02(39.9961, 116.4801)
    assert location == f"{longitude:.6f},{latitude:.6f}"
    assert location != "116.480100,39.996100"


@pytest.mark.asyncio
async def test_amap_client_passes_a_gcj02_fix_through_unchanged() -> None:
    fake = FakeAmap()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        geocoder = AmapGeocoder(
            geocode_config(coordinate_system="gcj02"), "secret-key", http
        )
        await geocoder.reverse(39.9961, 116.4801)
    assert fake.calls[0][0] == "116.480100,39.996100"


class _Recorder(logging.Handler):
    """Collect messages straight off a logger, whatever the root handler does."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.mark.asyncio
async def test_amap_client_returns_nothing_on_a_provider_error() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(FakeAmap(status=500))) as http:
        geocoder = AmapGeocoder(geocode_config(), "secret-key", http)
        assert await geocoder.reverse(39.9, 116.4) is None
    # A rejected key answers HTTP 200 with status=0, so the reason lives in the
    # body; without it in the log a bad key is indistinguishable from a place
    # nobody named, which is what "not resolved" already means.
    rejected = {
        "status": "0",
        "info": "SERVICE_NOT_AVAILABLE",
        "infocode": "10002",
    }
    recorder = _Recorder()
    geocode_logger = logging.getLogger("winnie_phone.geocode")
    previous_level = geocode_logger.level
    geocode_logger.addHandler(recorder)
    geocode_logger.setLevel(logging.WARNING)
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(FakeAmap(body=rejected))
        ) as http:
            geocoder = AmapGeocoder(geocode_config(), "secret-key", http)
            assert await geocoder.reverse(39.8, 116.3) is None
    finally:
        geocode_logger.removeHandler(recorder)
        geocode_logger.setLevel(previous_level)
    assert any(
        "10002" in message and "SERVICE_NOT_AVAILABLE" in message
        for message in recorder.messages
    ), recorder.messages


@pytest.mark.asyncio
async def test_photon_client_needs_no_key_and_sends_the_fix_unchanged() -> None:
    fake = FakePhoton()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        geocoder = build_geocoder(geocode_config(provider="photon"), key="", http=http)
        assert isinstance(geocoder, PhotonGeocoder)
        first = await geocoder.reverse(30.5928, 114.3058)
        # ~1 m away: the same cache entry, so the provider is not asked again.
        second = await geocoder.reverse(30.59281, 114.30581)
    assert first is not None and second is not None
    assert len(fake.calls) == 1
    assert fake.calls[0] == {"lat": "30.592800", "lon": "114.305800", "limit": "1"}
    # OSM is WGS-84, so there is no datum shift to apply or to get wrong.
    assert "key" not in fake.calls[0]
    assert "winnie" in fake.headers[0]["user-agent"]
    assert first.one_line() == "湖北省 武汉市 一元街道 横渡长江博物馆"


@pytest.mark.asyncio
async def test_photon_client_returns_nothing_on_a_provider_error() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(FakePhoton(status=503))) as http:
        geocoder = PhotonGeocoder(geocode_config(provider="photon"), http)
        assert await geocoder.reverse(30.5928, 114.3055) is None
    empty = {"type": "FeatureCollection", "features": []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(FakePhoton(body=empty))) as http:
        geocoder = PhotonGeocoder(geocode_config(provider="photon"), http)
        assert await geocoder.reverse(30.5928, 114.3055) is None


@pytest.mark.asyncio
async def test_build_geocoder_needs_an_enabled_config_and_a_key() -> None:
    async with httpx.AsyncClient() as http:
        assert isinstance(
            build_geocoder(geocode_config(enabled=False), key="k", http=http), NullGeocoder
        )
        assert isinstance(build_geocoder(geocode_config(), key="", http=http), NullGeocoder)
        assert isinstance(build_geocoder(geocode_config(), key="k", http=None), NullGeocoder)
        assert isinstance(build_geocoder(geocode_config(), key="k", http=http), AmapGeocoder)
        # Turning geocoding on with the keyless provider must not need a key.
        assert isinstance(
            build_geocoder(geocode_config(provider="photon"), key="", http=http),
            PhotonGeocoder,
        )


def test_a_typo_in_the_provider_name_fails_at_load() -> None:
    # "amapy" would otherwise be a silent NullGeocoder, and the console would say
    # "not resolved" for ever with nothing in the log to say why.
    with pytest.raises(ValidationError):
        PhoneGeocodeConfig(provider="amapy")


# --------------------------------------------------------------------------- #
# The sync path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sync_stores_the_address_with_the_fix(tmp_path: Path) -> None:
    geocoder = FakeGeocoder()
    engine, service, device = await service_for(
        tmp_path,
        PhoneConfig(enabled=True, geocode=geocode_config()),
        geocoder,
    )
    try:
        result = await service.sync(
            "local",
            uuid.UUID(str(device["id"])),
            SyncRequest(kind="location", items=[fix("fix-1")]),
        )
        assert result["accepted"] == 1
        rows = await service.locations("local")
        assert len(rows) == 1
        row = rows[0]
        assert row["address"] == "测试地址 39.996,116.480"
        assert row["province"] == "北京市"
        assert row["city"] == "北京市"
        assert row["district"] == "朝阳区"
        assert row["poi"] == "望京SOHO (12m)"
        assert geocoder.calls == [(39.996, 116.48)]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_failing_provider_never_fails_the_sync(tmp_path: Path) -> None:
    engine, service, device = await service_for(
        tmp_path,
        PhoneConfig(enabled=True, geocode=geocode_config()),
        FakeGeocoder(fail=True),
    )
    try:
        result = await service.sync(
            "local",
            uuid.UUID(str(device["id"])),
            SyncRequest(kind="location", items=[fix("fix-1")]),
        )
        assert result["accepted"] == 1
        rows = await service.locations("local")
        assert rows[0]["address"] == ""
        assert rows[0]["latitude"] == 39.996
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_without_a_provider_the_fix_keeps_its_coordinates(tmp_path: Path) -> None:
    engine, service, device = await service_for(
        tmp_path, PhoneConfig(enabled=True)
    )
    try:
        await service.sync(
            "local",
            uuid.UUID(str(device["id"])),
            SyncRequest(kind="location", items=[fix("fix-1")]),
        )
        rows = await service.locations("local")
        assert rows[0]["address"] == ""
        assert (await service.geocode_locations("local"))["configured"] is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_fills_old_fixes_in_bounded_batches(tmp_path: Path) -> None:
    config = PhoneConfig(enabled=True, geocode=geocode_config())
    engine, service, device = await service_for(tmp_path, config)
    device_id = uuid.UUID(str(device["id"]))
    try:
        await service.sync(
            "local",
            device_id,
            SyncRequest(
                kind="location",
                items=[
                    fix("fix-1", captured_at=(NOW - timedelta(hours=3)).isoformat()),
                    fix("fix-2", captured_at=(NOW - timedelta(hours=2)).isoformat()),
                ],
            ),
        )
        assert all(row["address"] == "" for row in await service.locations("local"))

        service.geocoder = FakeGeocoder()
        first = await service.geocode_locations("local", limit=1)
        assert first == {"configured": True, "geocoded": 1, "remaining": 1}
        second = await service.geocode_locations("local", limit=5)
        assert second == {"configured": True, "geocoded": 1, "remaining": 0}
        rows = await service.locations("local")
        assert all(row["district"] == "朝阳区" for row in rows)
        # A third pass has nothing to do.
        assert await service.geocode_locations("local") == {
            "configured": True,
            "geocoded": 0,
            "remaining": 0,
        }
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# The quota: an answer the database already holds is not bought twice
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_second_sync_at_the_same_place_does_not_call_the_provider(
    tmp_path: Path,
) -> None:
    """A phone on a desk syncs without moving; each sync must not cost a call.

    The provider cache only lives inside the provider, so this is the service's
    own reuse of the address it stored on the previous sync.
    """
    config = PhoneConfig(enabled=True, geocode=geocode_config())
    geocoder = FakeGeocoder()
    engine, service, device = await service_for(tmp_path, config, geocoder)
    device_id = uuid.UUID(str(device["id"]))
    try:
        for index, (latitude, longitude) in enumerate(
            [(39.9960, 116.4800), (39.9962, 116.4802), (39.9959, 116.4799)]
        ):
            await service.sync(
                "local",
                device_id,
                SyncRequest(
                    kind="location", items=[fix(f"fix-{index}", latitude, longitude)]
                ),
            )
        rows = await service.locations("local")
    finally:
        await engine.dispose()
    assert geocoder.calls == [(39.996, 116.48)]
    assert all(row["address"] for row in rows)


@pytest.mark.asyncio
async def test_a_restart_reuses_the_address_the_database_already_holds(
    tmp_path: Path,
) -> None:
    """The in-memory cache is gone after a restart; the database is not."""
    config = PhoneConfig(enabled=True, geocode=geocode_config())
    first = FakeGeocoder()
    engine, service, device = await service_for(tmp_path, config, first)
    device_id = uuid.UUID(str(device["id"]))
    try:
        await service.sync(
            "local", device_id, SyncRequest(kind="location", items=[fix("fix-1")])
        )
    finally:
        await engine.dispose()
    assert len(first.calls) == 1

    second = FakeGeocoder()
    engine, service, device = await service_for(tmp_path, config, second)
    device_id = uuid.UUID(str(device["id"]))
    try:
        await service.sync(
            "local",
            device_id,
            SyncRequest(kind="location", items=[fix("fix-2", 39.9961, 116.4801)]),
        )
        rows = await service.locations("local")
    finally:
        await engine.dispose()
    assert second.calls == []
    assert len(rows) == 2
    assert all(row["address"] == "测试地址 39.996,116.480" for row in rows)


@pytest.mark.asyncio
async def test_a_nearby_fix_in_another_cache_cell_is_still_reused(
    tmp_path: Path,
) -> None:
    """0.0011° away rounds to a different cell, and is still the same place."""
    config = PhoneConfig(enabled=True, geocode=geocode_config())
    geocoder = FakeGeocoder()
    engine, service, device = await service_for(tmp_path, config, geocoder)
    device_id = uuid.UUID(str(device["id"]))
    try:
        await service.sync(
            "local",
            device_id,
            SyncRequest(kind="location", items=[fix("fix-1", 39.9960, 116.4800)]),
        )
        await service.sync(
            "local",
            device_id,
            SyncRequest(kind="location", items=[fix("fix-2", 39.9970, 116.4810)]),
        )
        rows = await service.locations("local")
    finally:
        await engine.dispose()
    assert geocoder.calls == [(39.996, 116.48)]
    assert {row["address"] for row in rows} == {"测试地址 39.996,116.480"}


@pytest.mark.asyncio
async def test_a_fix_kilometres_away_is_not_answered_from_a_neighbour(
    tmp_path: Path,
) -> None:
    config = PhoneConfig(enabled=True, geocode=geocode_config())
    geocoder = FakeGeocoder()
    engine, service, device = await service_for(tmp_path, config, geocoder)
    device_id = uuid.UUID(str(device["id"]))
    try:
        await service.sync(
            "local", device_id, SyncRequest(kind="location", items=[fix("fix-1")])
        )
        await service.sync(
            "local",
            device_id,
            SyncRequest(
                kind="location",
                items=[
                    fix(
                        "fix-2",
                        40.01,
                        116.50,
                        captured_at=(NOW - timedelta(minutes=1)).isoformat(),
                    )
                ],
            ),
        )
        rows = await service.locations("local")
    finally:
        await engine.dispose()
    assert len(geocoder.calls) == 2
    assert rows[0]["address"] == "测试地址 40.010,116.500"


@pytest.mark.asyncio
async def test_an_answer_from_another_provider_is_not_reused(tmp_path: Path) -> None:
    """Switching provider must buy the new provider's answer, not inherit the old."""
    config = PhoneConfig(enabled=True, geocode=geocode_config())
    first = FakeGeocoder()
    engine, service, device = await service_for(tmp_path, config, first)
    device_id = uuid.UUID(str(device["id"]))
    try:
        await service.sync(
            "local", device_id, SyncRequest(kind="location", items=[fix("fix-1")])
        )
    finally:
        await engine.dispose()

    other = FakeGeocoder(name="amap")
    engine, service, device = await service_for(tmp_path, config, other)
    device_id = uuid.UUID(str(device["id"]))
    try:
        await service.sync(
            "local",
            device_id,
            SyncRequest(kind="location", items=[fix("fix-2", 39.9961, 116.4801)]),
        )
        rows = await service.locations("local")
    finally:
        await engine.dispose()
    assert len(other.calls) == 1
    assert {row["address"] for row in rows} == {"测试地址 39.996,116.480"}
    assert {row["poi"] for row in rows} == {"望京SOHO (12m)"}


@pytest.mark.asyncio
async def test_the_backfill_reuses_a_neighbour_it_just_resolved(tmp_path: Path) -> None:
    config = PhoneConfig(enabled=True, geocode=geocode_config())
    engine, service, device = await service_for(tmp_path, config)
    device_id = uuid.UUID(str(device["id"]))
    try:
        await service.sync(
            "local",
            device_id,
            SyncRequest(
                kind="location",
                items=[
                    fix("fix-1", 39.9960, 116.4800, captured_at=(NOW - timedelta(hours=2)).isoformat()),
                    fix("fix-2", 39.9970, 116.4810, captured_at=(NOW - timedelta(hours=1)).isoformat()),
                ],
            ),
        )
        geocoder = FakeGeocoder()
        service.geocoder = geocoder
        result = await service.geocode_locations("local", limit=5)
        rows = await service.locations("local")
    finally:
        await engine.dispose()
    assert result == {"configured": True, "geocoded": 2, "remaining": 0}
    assert len(geocoder.calls) == 1
    assert all(row["address"] for row in rows)


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


@pytest.fixture
def geocode_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv(KEY_ENV, "secret-key")
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        # A Settings() that names no database falls back to data/winnie.db, so
        # these tests used to sync into the owner's real phone data — and the
        # row they left there was then a legitimate reuse candidate for the next
        # run. Every fixture that builds the app names its own database.
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'geocode-api.db'}"
        ),
        phone=PhoneConfig(enabled=True, geocode=geocode_config()),
    )


@pytest_asyncio.fixture
async def geocode_client(
    geocode_settings: Settings,
) -> AsyncIterator[tuple[httpx.AsyncClient, FakeAmap]]:
    fake = FakeAmap()
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake), timeout=5.0)
    app = create_app(settings=geocode_settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, fake
    await http.aclose()


@pytest.mark.asyncio
async def test_location_endpoint_returns_the_address(
    geocode_client: tuple[httpx.AsyncClient, FakeAmap],
) -> None:
    client, fake = geocode_client
    device = (
        await client.post(
            "/api/v1/phone/devices",
            json={"device_key": "phone-key-geocode", "name": "Pixel"},
        )
    ).json()
    response = await client.post(
        f"/api/v1/phone/devices/{device['id']}/sync",
        json={"kind": "location", "items": [fix("fix-1")]},
    )
    assert response.status_code == 200, response.text
    assert len(fake.calls) == 1

    rows = (await client.get("/api/v1/phone/locations")).json()
    assert rows[0]["address"] == "北京市朝阳区望京街道阜通东大街6号"
    assert rows[0]["city"] == "北京市"
    assert rows[0]["poi"] == "望京SOHO (12m)"

    # The backfill endpoint answers with what it did, and needs no work here.
    backfill = await client.post("/api/v1/phone/locations/geocode", params={"limit": 5})
    assert backfill.status_code == 200
    assert backfill.json() == {"configured": True, "geocoded": 0, "remaining": 0}
