"""Map the supplier registry's gateway routes onto the proxy's route type."""

from __future__ import annotations

from winnie_gateway.routes import Route
from winnie_llm import ProviderRegistry


def gateway_route_table(registry: ProviderRegistry) -> dict[str, Route]:
    return {
        entry.name: Route(
            name=entry.name,
            protocol=entry.protocol,
            base_url=entry.base_url,
        )
        for entry in registry.route_entries()
    }
