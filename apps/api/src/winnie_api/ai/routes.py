"""Build the gateway's route table, including each route's server credential.

A route is a supplier endpoint plus the Winnie key policy in front of it. The
vendor credential is resolved here, once per table build, from the same
winnie-llm environment the internal providers use: the gateway no longer asks
the caller for a vendor key.

Two sources can produce routes. The admin registry is authoritative when it is
enabled; ``config.yaml`` is the fallback for a gateway-only deployment, which is
why a ``RouteConfig`` can name a vendor too.
"""

from __future__ import annotations

from collections.abc import Iterable

from winnie_gateway.config import GatewayConfig
from winnie_gateway.routes import Route
from winnie_llm import ApiKeys, ProviderRegistry, RouteEntry


def _credential(entry: RouteEntry, keys: ApiKeys) -> tuple[str, str]:
    """``(key, variable name)`` for a route, empty when it has no supplier."""
    if not entry.vendor:
        return "", ""
    return keys.get(entry.vendor), keys.env_name(entry.vendor)


def _table(entries: Iterable[RouteEntry], keys: ApiKeys) -> dict[str, Route]:
    table: dict[str, Route] = {}
    for entry in entries:
        api_key, api_key_env = _credential(entry, keys)
        table[entry.name] = Route(
            name=entry.name,
            protocol=entry.protocol,
            base_url=entry.base_url,
            vendor=entry.vendor,
            api_key=api_key,
            api_key_env=api_key_env,
        )
    return table


def gateway_route_table(registry: ProviderRegistry, keys: ApiKeys) -> dict[str, Route]:
    """Routes the admin registry exposes, with their supplier credentials."""
    return _table(registry.route_entries(), keys)


def config_route_table(config: GatewayConfig, keys: ApiKeys) -> dict[str, Route]:
    """Routes declared in ``config.yaml``, for a gateway without the registry."""
    return _table(
        [
            RouteEntry(
                name=name,
                protocol=route.protocol,
                base_url=route.base_url,
                vendor=route.vendor,
            )
            for name, route in config.routes.items()
        ],
        keys,
    )
