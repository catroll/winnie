"""Provider balance snapshots for the console.

Only vendors with a documented balance or credits endpoint are queried, and
only with a server-side key from the environment. The gateway never stores a
vendor key, so these snapshots cover internal jobs and the assistant, not
client traffic through a proxy route.

The adapters are chosen by the integration's host, not by a new registry
field: the endpoint is a property of the vendor, and an unknown host is shown
as "not supported" rather than guessed at.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx

from winnie_llm import IntegrationRecord, ProviderRegistry


@dataclass(frozen=True)
class BalanceLine:
    label: str
    value: str


@dataclass(frozen=True)
class ProviderBalance:
    integration_id: str
    name: str
    label: str
    kind: str
    base_url: str
    supported: bool
    currency: str = ""
    total: str = ""
    lines: tuple[BalanceLine, ...] = ()
    fetched_at: str = ""
    error: str = ""
    cached: bool = False
    endpoint: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "integration_id": self.integration_id,
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "base_url": self.base_url,
            "supported": self.supported,
            "currency": self.currency,
            "total": self.total,
            "lines": [
                {"label": line.label, "value": line.value} for line in self.lines
            ],
            "fetched_at": self.fetched_at,
            "error": self.error,
            "cached": self.cached,
            "endpoint": self.endpoint,
        }


@dataclass(frozen=True)
class Adapter:
    name: str
    fetch: Callable[
        [httpx.AsyncClient, str, str, httpx.Timeout],
        Awaitable[tuple[str, str, list[BalanceLine]]],
    ]
    path: str


class BalanceUnavailable(RuntimeError):
    """The provider answered, but not with a usable balance."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _origin(base_url: str) -> str:
    parsed = urlsplit(base_url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise BalanceUnavailable(f"invalid base_url: {base_url!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


async def _get(
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    timeout: httpx.Timeout,
) -> httpx.Response:
    return await client.get(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=timeout,
    )


async def _json(response: httpx.Response, url: str) -> Any:
    if response.status_code >= 400:
        body = response.text[:300]
        raise BalanceUnavailable(f"HTTP {response.status_code} from {url}: {body}")
    try:
        return response.json()
    except ValueError as exc:
        raise BalanceUnavailable(f"{url} returned non-JSON content") from exc


async def _deepseek(
    client: httpx.AsyncClient, base_url: str, api_key: str, timeout: httpx.Timeout
) -> tuple[str, str, list[BalanceLine]]:
    url = f"{_origin(base_url)}/user/balance"
    payload = await _json(await _get(client, url, api_key, timeout), url)
    infos = payload.get("balance_infos") if isinstance(payload, dict) else None
    if not isinstance(infos, list) or not infos:
        raise BalanceUnavailable("the response did not include a balance")
    first = infos[0] if isinstance(infos[0], dict) else {}
    currency = str(first.get("currency") or "")
    total = str(first.get("total_balance") or "")
    if not total:
        raise BalanceUnavailable("the response did not include total_balance")
    lines = [
        BalanceLine("Granted balance", str(first.get("granted_balance") or "0")),
        BalanceLine("Topped up", str(first.get("topped_up_balance") or "0")),
    ]
    if payload.get("is_available") is False:
        lines.append(BalanceLine("Status", "below the call threshold"))
    for extra in infos[1:]:
        if isinstance(extra, dict):
            lines.append(
                BalanceLine(
                    f"{extra.get('currency') or 'other'} total",
                    str(extra.get("total_balance") or "0"),
                )
            )
    return currency, total, lines


async def _openrouter(
    client: httpx.AsyncClient, base_url: str, api_key: str, timeout: httpx.Timeout
) -> tuple[str, str, list[BalanceLine]]:
    url = f"{_origin(base_url)}/api/v1/credits"
    payload = await _json(await _get(client, url, api_key, timeout), url)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise BalanceUnavailable("the response did not include credit data")
    purchased = _decimal(data.get("total_credits"), "total_credits")
    used = _decimal(data.get("total_usage"), "total_usage")
    return (
        "USD",
        _format_decimal(purchased - used),
        [
            BalanceLine("Purchased", _format_decimal(purchased)),
            BalanceLine("Used", _format_decimal(used)),
        ],
    )


async def _moonshot(
    client: httpx.AsyncClient, base_url: str, api_key: str, timeout: httpx.Timeout
) -> tuple[str, str, list[BalanceLine]]:
    url = f"{_origin(base_url)}/v1/users/me/balance"
    payload = await _json(await _get(client, url, api_key, timeout), url)
    if not isinstance(payload, dict):
        raise BalanceUnavailable("the response was not an object")
    if payload.get("status") is False:
        raise BalanceUnavailable(str(payload.get("scode") or "the query failed"))
    data = payload.get("data")
    if not isinstance(data, dict):
        raise BalanceUnavailable("the response did not include balance data")
    available = _decimal(data.get("available_balance"), "available_balance")
    return (
        "USD",
        _format_decimal(available),
        [
            BalanceLine(
                "Voucher balance",
                _format_decimal(
                    _decimal(data.get("voucher_balance"), "voucher_balance")
                ),
            ),
            BalanceLine(
                "Cash balance",
                _format_decimal(_decimal(data.get("cash_balance"), "cash_balance")),
            ),
        ],
    )


async def _siliconflow(
    client: httpx.AsyncClient, base_url: str, api_key: str, timeout: httpx.Timeout
) -> tuple[str, str, list[BalanceLine]]:
    url = f"{_origin(base_url)}/v1/user/info"
    payload = await _json(await _get(client, url, api_key, timeout), url)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise BalanceUnavailable("the response did not include user data")
    total = _decimal(data.get("totalBalance"), "totalBalance")
    currency = "CNY" if (urlsplit(base_url).hostname or "").endswith(".cn") else "USD"
    return (
        currency,
        _format_decimal(total),
        [
            BalanceLine(
                "Balance",
                _format_decimal(_decimal(data.get("balance"), "balance")),
            ),
            BalanceLine(
                "Charge balance",
                _format_decimal(_decimal(data.get("chargeBalance"), "chargeBalance")),
            ),
        ],
    )


_ADAPTERS: dict[str, Adapter] = {
    "api.deepseek.com": Adapter("deepseek", _deepseek, "/user/balance"),
    "openrouter.ai": Adapter("openrouter", _openrouter, "/api/v1/credits"),
    "www.openrouter.ai": Adapter("openrouter", _openrouter, "/api/v1/credits"),
    "api.moonshot.ai": Adapter(
        "moonshot", _moonshot, "/v1/users/me/balance"
    ),
    "api.moonshot.cn": Adapter(
        "moonshot", _moonshot, "/v1/users/me/balance"
    ),
    "api.siliconflow.cn": Adapter("siliconflow", _siliconflow, "/v1/user/info"),
    "api.siliconflow.com": Adapter("siliconflow", _siliconflow, "/v1/user/info"),
}


def adapter_for(base_url: str) -> Adapter | None:
    host = (urlsplit(base_url.strip()).hostname or "").lower()
    return _ADAPTERS.get(host)


def _decimal(value: Any, field_name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise BalanceUnavailable(f"the response did not include {field_name}")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BalanceUnavailable(f"{field_name} is not a number: {value!r}") from exc


def _format_decimal(value: Decimal) -> str:
    return format(value, "f")


class BalanceService:
    """Fetch and cache one snapshot per non-fake integration."""

    def __init__(
        self,
        *,
        ai: ProviderRegistry,
        http: httpx.AsyncClient,
        ttl_seconds: float = 300.0,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.ai = ai
        self.http = http
        self.ttl_seconds = ttl_seconds
        self.timeout = httpx.Timeout(timeout_seconds, connect=5.0)
        self._cache: dict[str, tuple[float, ProviderBalance]] = {}
        self._lock = asyncio.Lock()

    async def snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        integrations = [
            integration
            for integration in self.ai.state().integrations
            if integration.kind != "fake"
        ]
        async with self._lock:
            balances = await asyncio.gather(
                *(
                    self._one(integration, refresh=refresh)
                    for integration in integrations
                )
            )
        return {
            "ttl_seconds": int(self.ttl_seconds),
            "balances": [balance.to_json() for balance in balances],
        }

    async def _one(
        self, integration: IntegrationRecord, *, refresh: bool
    ) -> ProviderBalance:
        cached = self._cache.get(integration.id)
        fresh = (
            cached is not None
            and not refresh
            and (time.monotonic() - cached[0]) < self.ttl_seconds
        )
        if fresh and cached is not None:
            return replace(cached[1], cached=True)

        result = await self._fetch(integration)
        self._cache[integration.id] = (time.monotonic(), result)
        return result

    async def _fetch(self, integration: IntegrationRecord) -> ProviderBalance:
        base = ProviderBalance(
            integration_id=integration.id,
            name=integration.name,
            label=integration.label or integration.name,
            kind=integration.kind,
            base_url=integration.base_url,
            supported=True,
            fetched_at=now_iso(),
        )
        adapter = adapter_for(integration.base_url)
        if adapter is None:
            return replace(
                base,
                supported=False,
                error=(
                    "this provider has no public balance endpoint; the console "
                    "shows configured prices and recorded usage instead"
                ),
            )
        base = replace(base, endpoint=f"{_origin(integration.base_url)}{adapter.path}")
        key_name = self.ai.keys.env_name(integration.vendor)
        api_key = self.ai.keys.get(integration.vendor)
        if not api_key:
            return replace(
                base,
                error=(
                    f"{key_name} is not set for supplier "
                    f"{integration.vendor!r}; add it to {self.ai.keys.path}"
                ),
            )
        try:
            currency, total, lines = await adapter.fetch(
                self.http, integration.base_url, api_key, self.timeout
            )
        except BalanceUnavailable as exc:
            return replace(base, error=str(exc))
        except httpx.HTTPError as exc:
            return replace(base, error=f"cannot reach {base.endpoint}: {exc}")
        except Exception as exc:  # noqa: BLE001 - one provider must not fail the page
            return replace(base, error=f"{type(exc).__name__}: {exc}")
        return replace(
            base, currency=currency, total=total, lines=tuple(lines)
        )
