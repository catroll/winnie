"""Reverse geocoding: a coordinate becomes a readable place.

The phone sends a fix, never an address. Resolving one needs a provider, a key
and an HTTP call, none of which belong on the phone: it would repeat the same
lookup every sync, drain battery, and send the owner's position from the device
to a third party. The server geocodes what it accepts instead, keeps the result
in the row, and caches by rounded coordinate so a phone sitting at home costs one
call, not one call every sync.

A provider is a port. :class:`AmapGeocoder` is the shipped implementation because
Amap has the best address and POI coverage in mainland China; with no key
configured the service uses :class:`NullGeocoder`, every fix keeps its raw
coordinates, and the console shows exactly what it showed before.

The address is stored as::

    {"formatted": "北京市朝阳区望京街道…", "province": "北京市",
     "city": "北京市", "district": "朝阳区", "township": "望京街道",
     "street": "阜通东大街", "street_number": "6号",
     "poi": "望京SOHO (12m)", "provider": "amap", "at": "2026-11-01T…"}
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from .config import PhoneGeocodeConfig

AMAP_ENDPOINT = "https://restapi.amap.com/v3/geocode/regeo"

# Coordinates are cached at ~111 m (3 decimals). A phone that has not moved gets
# the cached answer; two fixes 20 m apart must not cost two API calls.
CACHE_PRECISION = 3


@dataclass(frozen=True)
class Address:
    """One resolved place, in the shape the row payload keeps."""

    formatted: str
    province: str = ""
    city: str = ""
    district: str = ""
    township: str = ""
    street: str = ""
    street_number: str = ""
    poi: str = ""
    provider: str = ""
    at: datetime | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "formatted": self.formatted,
            "province": self.province,
            "city": self.city,
            "district": self.district,
            "township": self.township,
            "street": self.street,
            "street_number": self.street_number,
            "poi": self.poi,
            "provider": self.provider,
            "at": (self.at or datetime.now(timezone.utc)).isoformat(),
        }

    def one_line(self) -> str:
        """``省 市 区 具体位置`` with the empty parts dropped."""
        return _join_unique(
            [self.province, self.city, self.district, self.poi or self.formatted]
        ) or self.formatted


def address_fields(payload: dict[str, Any]) -> dict[str, str]:
    """Read the address keys back out of a location row payload."""
    address = payload.get("address")
    if not isinstance(address, dict):
        return {"address": "", "province": "", "city": "", "district": "", "poi": ""}
    return {
        "address": _text(address.get("formatted")),
        "province": _text(address.get("province")),
        "city": _text(address.get("city")),
        "district": _text(address.get("district")),
        "poi": _text(address.get("poi")),
    }


def cache_key(latitude: float, longitude: float) -> tuple[float, float]:
    return (round(latitude, CACHE_PRECISION), round(longitude, CACHE_PRECISION))


class Geocoder(Protocol):
    """One reverse-geocoding provider."""

    name: str

    async def reverse(self, latitude: float, longitude: float) -> Address | None:
        ...

    async def aclose(self) -> None:
        ...


class NullGeocoder:
    """The provider that is not configured: never resolves, never fails."""

    name = "none"

    async def reverse(self, latitude: float, longitude: float) -> Address | None:
        return None

    async def aclose(self) -> None:
        return None


class AmapGeocoder:
    """Amap (高德) reverse geocoding over its restapi.

    The provider's response is not trusted: ``status`` is a string, an empty
    field arrives as ``[]`` rather than ``""``, and every text field can be
    absent. Parsing therefore coerces through :func:`_text` and composes a
    readable address from whatever is present instead of taking one field.
    """

    name = "amap"

    def __init__(
        self,
        config: PhoneGeocodeConfig,
        key: str,
        http: httpx.AsyncClient,
    ) -> None:
        self._config = config
        self._key = key
        self._http = http
        self._cache: OrderedDict[tuple[float, float], Address | None] = OrderedDict()
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def reverse(self, latitude: float, longitude: float) -> Address | None:
        key = cache_key(latitude, longitude)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        address = await self._fetch(latitude, longitude)
        self._remember(key, address)
        return address

    async def _fetch(self, latitude: float, longitude: float) -> Address | None:
        async with self._lock:
            # A personal Amap key has a low QPS ceiling; one call at a time with
            # a small gap is enough for a device that syncs every few minutes.
            gap = self._config.min_interval_seconds - (time.monotonic() - self._last_call)
            if gap > 0:
                await asyncio.sleep(gap)
            try:
                response = await self._http.get(
                    AMAP_ENDPOINT,
                    params={
                        "key": self._key,
                        "location": f"{longitude:.6f},{latitude:.6f}",
                        "extensions": "all",
                        "radius": 1000,
                    },
                    timeout=self._config.timeout_seconds,
                )
                self._last_call = time.monotonic()
                if response.status_code != 200:
                    return None
                return parse_amap(response.json(), provider=self.name)
            except (httpx.HTTPError, ValueError):
                return None

    def _remember(self, key: tuple[float, float], address: Address | None) -> None:
        if self._config.cache_entries <= 0:
            return
        self._cache[key] = address
        self._cache.move_to_end(key)
        while len(self._cache) > self._config.cache_entries:
            self._cache.popitem(last=False)

    async def aclose(self) -> None:
        return None


def parse_amap(payload: Any, *, provider: str = "amap") -> Address | None:
    """Turn an Amap ``regeo`` body into an :class:`Address`, or ``None``."""
    if not isinstance(payload, dict) or str(payload.get("status")) != "1":
        return None
    regeocode = payload.get("regeocode")
    if not isinstance(regeocode, dict):
        return None
    component = regeocode.get("addressComponent")
    component = component if isinstance(component, dict) else {}

    province = _text(component.get("province"))
    district = _text(component.get("district"))
    street_number = component.get("streetNumber")
    street_number = street_number if isinstance(street_number, dict) else {}
    street = _text(street_number.get("street"))
    number = _text(street_number.get("number"))
    township = _text(component.get("township"))
    # A municipality reports an empty ``city``; the province is the city there.
    city = _text(component.get("city")) or province

    poi = _nearest_poi(regeocode, component)
    formatted = _text(regeocode.get("formatted_address"))
    if not formatted:
        formatted = _join_unique(
            [province, city, district, township, f"{street}{number}".strip()]
        )
    if not (formatted or poi):
        return None

    return Address(
        formatted=formatted,
        province=province,
        city=city,
        district=district,
        township=township,
        street=street,
        street_number=number,
        poi=poi,
        provider=provider,
    )


def _nearest_poi(regeocode: dict[str, Any], component: dict[str, Any]) -> str:
    for source in (regeocode.get("pois"), component.get("pois")):
        if isinstance(source, list):
            for entry in source:
                if isinstance(entry, dict):
                    name = _text(entry.get("name"))
                    if name:
                        distance = _text(entry.get("distance"))
                        return f"{name} ({distance}m)" if distance else name
    return ""


def _join_unique(parts: list[str]) -> str:
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return " ".join(seen)


def _text(value: Any) -> str:
    """Amap sends ``[]`` for an empty field; only a string is real text."""
    if isinstance(value, str):
        return value.strip()
    return ""


def build_geocoder(
    config: PhoneGeocodeConfig,
    *,
    key: str,
    http: httpx.AsyncClient | None,
) -> Geocoder:
    """Return the configured provider, or the null one.

    A missing key is not an error: the owner may simply not have one, and the
    rest of the phone data is still worth syncing.
    """
    if not config.enabled or not key or http is None:
        return NullGeocoder()
    return AmapGeocoder(config, key, http)
