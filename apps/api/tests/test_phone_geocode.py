"""Reverse geocoding: provider parsing, caching, the sync path and backfill.

The provider is exercised through an ``httpx.MockTransport`` so the assertions are
about our parsing of Amap's shapes (an empty ``city``, an ``[]`` for a missing
string, a POI list) rather than about the network. The service tests use a fake
geocoder so a failure can be injected where it matters.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from winnie_api.main import create_app
from winnie_api.settings import Settings
from winnie_core.db import create_all, create_engine, create_session_factory
from winnie_phone.config import PhoneConfig, PhoneGeocodeConfig
from winnie_phone.geocode import Address, AmapGeocoder, NullGeocoder, build_geocoder, parse_amap
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


class FakeAmap:
    """Serve the regeo endpoint and count calls per coordinate."""

    def __init__(self, body: dict | None = None, status: int = 200) -> None:
        self.calls: list[tuple[str, str]] = []
        self.body = body if body is not None else AMAP_OK
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.params.get("location", ""), request.url.params.get("key", "")))
        return httpx.Response(self.status, json=self.body)


class FakeGeocoder:
    name = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[float, float]] = []
        self.fail = fail

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
            provider="fake",
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
    # Amap wants longitude first.
    assert location == "116.480100,39.996100"


@pytest.mark.asyncio
async def test_amap_client_returns_nothing_on_a_provider_error() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(FakeAmap(status=500))) as http:
        geocoder = AmapGeocoder(geocode_config(), "secret-key", http)
        assert await geocoder.reverse(39.9, 116.4) is None
    async with httpx.AsyncClient(transport=httpx.MockTransport(FakeAmap(body={"status": "0"}))) as http:
        geocoder = AmapGeocoder(geocode_config(), "secret-key", http)
        assert await geocoder.reverse(39.8, 116.3) is None


@pytest.mark.asyncio
async def test_build_geocoder_needs_an_enabled_config_and_a_key() -> None:
    async with httpx.AsyncClient() as http:
        assert isinstance(
            build_geocoder(geocode_config(enabled=False), key="k", http=http), NullGeocoder
        )
        assert isinstance(build_geocoder(geocode_config(), key="", http=http), NullGeocoder)
        assert isinstance(build_geocoder(geocode_config(), key="k", http=None), NullGeocoder)
        assert isinstance(build_geocoder(geocode_config(), key="k", http=http), AmapGeocoder)


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
# The HTTP surface
# --------------------------------------------------------------------------- #


@pytest.fixture
def geocode_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv(KEY_ENV, "secret-key")
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
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
