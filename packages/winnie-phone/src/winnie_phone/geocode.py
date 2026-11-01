"""Reverse geocoding: a coordinate becomes a readable place.

The phone sends a fix, never an address. Resolving one needs a provider and an
HTTP call, none of which belong on the phone: it would repeat the same lookup
every sync, drain battery, and send the owner's position from the device to a
third party. The server geocodes what it accepts instead, keeps the result in
the row, and caches by rounded coordinate so a phone sitting at home costs one
call, not one call every sync.

Two providers ship, and the difference is a trade the owner makes by name:

``photon``
    komoot's hosted index over OpenStreetMap. Needs no key, no account and no
    quota registration, and takes the WGS-84 coordinates the phone reports
    unchanged. It answers with the nearest *named* feature — a house number, a
    street, a museum, a hamlet — plus the city and district around it. That is
    the "roughly where" answer, and it is the shipped default.
``amap``
    高德, street-level postal addresses with the best coverage in mainland
    China, but it needs a free key, and its coordinates are GCJ-02 while the
    phone reports WGS-84. :func:`wgs84_to_gcj02` bridges that; skip it and every
    address is a few hundred metres off, which in a city is the next street.

With neither configured the service uses :class:`NullGeocoder`, every fix keeps
its raw coordinates, and the console shows "not resolved" — the coordinates are
still there and still open in a map, so nothing is lost either way.

The address is stored as::

    {"formatted": "北京市朝阳区望京街道…", "province": "北京市",
     "city": "北京市", "district": "朝阳区", "township": "望京街道",
     "street": "阜通东大街", "street_number": "6号",
     "poi": "望京SOHO (12m)", "provider": "amap", "at": "2026-11-01T…"}
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from .config import PhoneGeocodeConfig

logger = logging.getLogger(__name__)

AMAP_ENDPOINT = "https://restapi.amap.com/v3/geocode/regeo"
PHOTON_ENDPOINT = "https://photon.komoot.io/reverse"

# A public community endpoint, not a paid service: name the client so a spike in
# its logs can be attributed instead of looking like a script.
USER_AGENT = "winnie-phone (+https://github.com/catroll/winnie)"

# Coordinates are cached at ~111 m (3 decimals). A phone that has not moved gets
# the cached answer; two fixes 20 m apart must not cost two API calls.
CACHE_PRECISION = 3

# Photon answers with the nearest *named* feature, which for a fix in open country
# can be a landuse polygon or a hamlet kilometres away. Past this the name says
# more about OSM's coverage than about where the owner was, so the fix keeps its
# coordinates rather than acquiring a misleading address.
MAX_FEATURE_KM = 5.0


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


class _HttpProvider:
    """Cache, call spacing and HTTP for one provider.

    Only the request parameters and the parsing differ between providers, so the
    shell is shared. :meth:`reverse` never raises: a provider failure leaves the
    row with its coordinates, which is what the console already shows, and logs
    the provider's own words so a rejected key is not indistinguishable from a
    place OpenStreetMap has no name for.
    """

    name = ""
    endpoint = ""

    def __init__(self, config: PhoneGeocodeConfig, http: httpx.AsyncClient) -> None:
        self._config = config
        self._http = http
        self._cache: OrderedDict[tuple[float, float], Address | None] = OrderedDict()
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    def params(self, latitude: float, longitude: float) -> dict[str, Any]:
        raise NotImplementedError

    def parse(
        self, payload: Any, latitude: float, longitude: float
    ) -> Address | None:
        raise NotImplementedError

    def explain(self, payload: Any) -> str:
        """The provider's reason for resolving nothing, for the log."""
        return "no address in the response"

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
            # Both hosted services are rate-limited, and a personal Amap key has
            # a low QPS ceiling; one call at a time with a small gap is enough
            # for a device that syncs every few minutes.
            gap = self._config.min_interval_seconds - (
                time.monotonic() - self._last_call
            )
            if gap > 0:
                await asyncio.sleep(gap)
            try:
                response = await self._http.get(
                    self.endpoint,
                    params=self.params(latitude, longitude),
                    headers={"User-Agent": USER_AGENT},
                    timeout=self._config.timeout_seconds,
                )
                self._last_call = time.monotonic()
                if response.status_code != 200:
                    logger.warning(
                        "%s reverse geocoding answered HTTP %s",
                        self.name,
                        response.status_code,
                    )
                    return None
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                logger.warning(
                    "%s reverse geocoding failed", self.name, exc_info=True
                )
                return None

        address = self.parse(payload, latitude, longitude)
        if address is None:
            # A rejected key, an exhausted quota and a place nobody named all
            # end up as "not resolved" in the console; the log is the only place
            # that can tell them apart, so it must not stay silent.
            logger.warning(
                "%s reverse geocoding resolved nothing: %s",
                self.name,
                self.explain(payload),
            )
        return address

    def _remember(self, key: tuple[float, float], address: Address | None) -> None:
        if self._config.cache_entries <= 0:
            return
        self._cache[key] = address
        self._cache.move_to_end(key)
        while len(self._cache) > self._config.cache_entries:
            self._cache.popitem(last=False)

    async def aclose(self) -> None:
        return None


class AmapGeocoder(_HttpProvider):
    """Amap (高德) reverse geocoding over its restapi.

    The provider's response is not trusted: ``status`` is a string, an empty
    field arrives as ``[]`` rather than ``""``, and every text field can be
    absent. Parsing therefore coerces through :func:`_text` and composes a
    readable address from whatever is present instead of taking one field.
    """

    name = "amap"
    endpoint = AMAP_ENDPOINT

    def __init__(
        self,
        config: PhoneGeocodeConfig,
        key: str,
        http: httpx.AsyncClient,
    ) -> None:
        super().__init__(config, http)
        self._key = key

    def params(self, latitude: float, longitude: float) -> dict[str, Any]:
        if self._config.coordinate_system == "wgs84":
            latitude, longitude = wgs84_to_gcj02(latitude, longitude)
        return {
            "key": self._key,
            "location": f"{longitude:.6f},{latitude:.6f}",
            "extensions": "all",
            "radius": 1000,
        }

    def parse(
        self, payload: Any, latitude: float, longitude: float
    ) -> Address | None:
        return parse_amap(payload, provider=self.name)

    def explain(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return "unreadable body"
        return (
            f"status={payload.get('status')} "
            f"infocode={payload.get('infocode')} "
            f"info={payload.get('info')}"
        )


class PhotonGeocoder(_HttpProvider):
    """Photon: komoot's hosted index over OpenStreetMap. No key, no account.

    The trade is precision for setup: the answer is a named OSM feature near the
    fix with the city and district around it, not a composed postal address. OSM
    is WGS-84, so the phone's coordinates go out unchanged and there is no datum
    shift to get wrong.
    """

    name = "photon"
    endpoint = PHOTON_ENDPOINT

    def params(self, latitude: float, longitude: float) -> dict[str, Any]:
        # ``lang`` accepts only default/de/en/fr and answers HTTP 400 for
        # anything else; the default is the local name, which is what an address
        # in China should be.
        return {"lat": f"{latitude:.6f}", "lon": f"{longitude:.6f}", "limit": 1}

    def parse(
        self, payload: Any, latitude: float, longitude: float
    ) -> Address | None:
        return parse_photon(payload, provider=self.name, near=(latitude, longitude))

    def explain(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return "unreadable body"
        features = payload.get("features")
        if not isinstance(features, list):
            return "unreadable body"
        if not features:
            return "no OSM feature near the fix"
        return "the nearest feature is unnamed or further than the maximum"


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


def parse_photon(
    payload: Any,
    *,
    provider: str = "photon",
    near: tuple[float, float] | None = None,
) -> Address | None:
    """Turn a Photon reverse response into an :class:`Address`, or ``None``.

    Photon spells a place in whatever keys OpenStreetMap happened to tag: a
    municipality carries ``city`` but no ``state``, a county seat carries
    ``county`` instead of ``district``, a rural road has no ``name`` at all.
    Everything below is therefore optional and the address is composed from what
    is present, the same rule :func:`parse_amap` follows.
    """
    if not isinstance(payload, dict):
        return None
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        return None
    feature = features[0]
    if not isinstance(feature, dict):
        return None
    properties = feature.get("properties")
    properties = properties if isinstance(properties, dict) else {}

    city = _text(properties.get("city"))
    # A municipality has no state, and a district under a county reports county.
    province = _text(properties.get("state")) or city
    district = _text(properties.get("district")) or _text(properties.get("county"))
    township = _text(properties.get("locality"))
    street = _text(properties.get("street"))
    number = _text(properties.get("housenumber"))
    name = _text(properties.get("name"))

    if near is not None:
        point = _feature_point(feature)
        if point is not None and _distance_km(near, point) > MAX_FEATURE_KM:
            return None

    formatted = _join_unique(
        [province, city, district, township, f"{street} {number}".strip(), name]
    )
    if not formatted:
        return None

    return Address(
        formatted=formatted,
        province=province,
        city=city,
        district=district,
        township=township,
        street=street,
        street_number=number,
        poi=name,
        provider=provider,
    )


def wgs84_to_gcj02(latitude: float, longitude: float) -> tuple[float, float]:
    """Shift a WGS-84 fix into GCJ-02, the datum Amap's API expects.

    The phone reports what the GNSS receiver gives it, which is WGS-84; Amap
    answers in, and wants, the offset datum Chinese law requires. Sending WGS-84
    to Amap is not an error it can report — the coordinate is valid, it is just
    in the wrong place, by ~300-600 m in a city, which resolves to a real
    address on the wrong street.

    Outside mainland China nothing is shifted: GCJ-02 is defined only there, and
    an offset applied abroad would be a wrong answer invented by us.
    """
    if _out_of_china(latitude, longitude):
        return latitude, longitude

    d_lat = _transform_lat(longitude - 105.0, latitude - 35.0)
    d_lon = _transform_lon(longitude - 105.0, latitude - 35.0)
    rad_lat = latitude / 180.0 * _PI
    magic = 1 - _EE * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * _PI)
    d_lon = (d_lon * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * _PI)
    return latitude + d_lat, longitude + d_lon


_A = 6378245.0  # Krasovsky 1940, the ellipsoid GCJ-02 is defined on.
_EE = 0.00669342162296594323
_PI = math.pi


def _transform_lat(x: float, y: float) -> float:
    ret = (
        -100.0
        + 2.0 * x
        + 3.0 * y
        + 0.2 * y * y
        + 0.1 * x * y
        + 0.2 * math.sqrt(abs(x))
    )
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * _PI) + 40.0 * math.sin(y / 3.0 * _PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * _PI) + 320 * math.sin(y * _PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * _PI) + 40.0 * math.sin(x / 3.0 * _PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * _PI) + 300.0 * math.sin(x / 30.0 * _PI)) * 2.0 / 3.0
    return ret


def _out_of_china(latitude: float, longitude: float) -> bool:
    return not (72.004 <= longitude <= 137.8347 and 0.8293 <= latitude <= 55.8271)


def _feature_point(feature: dict[str, Any]) -> tuple[float, float] | None:
    """The feature's own point, as (latitude, longitude)."""
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    try:
        longitude, latitude = float(coordinates[0]), float(coordinates[1])
    except (TypeError, ValueError):
        return None
    return latitude, longitude


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (latitude, longitude) pairs."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


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

    A missing key is not an error for a keyless provider, and it is not an error
    for Amap either: the owner may simply not have one, and the rest of the phone
    data is still worth syncing. An unknown provider name is a different matter —
    that is a typo, and answering it with a silent :class:`NullGeocoder` would
    hide it behind "not resolved" for ever — but
    :class:`~winnie_phone.config.PhoneGeocodeConfig` refuses the name before it
    reaches this function, so the branches below are exhaustive.
    """
    if not config.enabled or http is None:
        return NullGeocoder()
    if config.provider == "photon":
        return PhotonGeocoder(config, http)
    if not key:
        return NullGeocoder()
    return AmapGeocoder(config, key, http)
