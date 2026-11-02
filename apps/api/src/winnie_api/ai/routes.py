"""Build the gateway's route table, including each route's server credential.

A route is a supplier endpoint plus the Winnie key policy in front of it. The
vendor credential comes from the supplier record in the registry, the same
place an internal model call reads it from, so there is one credential per
supplier rather than one per role.

Two sources can produce routes. The admin registry is authoritative when it is
enabled; ``config.yaml`` is the fallback for a gateway-only deployment, which is
why a ``RouteConfig`` can name a vendor too. Only that fallback reads the
winnie-llm env backup, because it has no registry to read.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from winnie_gateway.config import GatewayConfig
from winnie_gateway.routes import Route
from winnie_llm import ApiKeys, ProviderRegistry, RouteEntry

# Returns ``(key, variable name)`` for a route's supplier, both empty when the
# route names no supplier.
Credential = Callable[[RouteEntry], "tuple[str, str]"]


def _table(entries: Iterable[RouteEntry], credential: Credential) -> dict[str, Route]:
    table: dict[str, Route] = {}
    for entry in entries:
        api_key, api_key_env = credential(entry)
        table[entry.name] = Route(
            name=entry.name,
            protocol=entry.protocol,
            base_url=entry.base_url,
            vendor=entry.vendor,
            api_key=api_key,
            api_key_env=api_key_env,
        )
    return table


def gateway_route_table(registry: ProviderRegistry) -> dict[str, Route]:
    """Routes the admin registry exposes, with their stored supplier keys."""
    return _table(
        registry.route_entries(),
        lambda entry: (
            (registry.vendor_key(entry.vendor), registry.key_env(entry.vendor))
            if entry.vendor
            else ("", "")
        ),
    )


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
        lambda entry: (
            (keys.get(entry.vendor), keys.env_name(entry.vendor))
            if entry.vendor
            else ("", "")
        ),
    )
