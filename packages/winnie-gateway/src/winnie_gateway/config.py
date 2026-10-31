"""Gateway settings, independent of the host application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from winnie_gateway.routes import Route


class RouteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["openai", "anthropic"]
    base_url: str


class GatewayConfig(BaseModel):
    """Everything the gateway needs from the host."""

    model_config = ConfigDict(extra="forbid")

    routes: dict[str, RouteConfig] = Field(default_factory=dict)
    log_root: Path
    anthropic_version: str = "2023-06-01"
    proxy_api_key: str = ""

    def route_table(self) -> dict[str, Route]:
        return {
            name: Route(name=name, protocol=cfg.protocol, base_url=cfg.base_url)
            for name, cfg in self.routes.items()
        }
