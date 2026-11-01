"""Gateway settings, independent of the host application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RouteConfig(BaseModel):
    """One route declared in ``config.yaml``.

    ``vendor`` names the supplier credential the host injects. It is required
    for a route to be usable: a route without it (or without a key in the
    winnie-llm environment) answers ``503`` instead of forwarding an
    unauthenticated request upstream.
    """

    model_config = ConfigDict(extra="forbid")

    protocol: Literal["openai", "anthropic"]
    base_url: str
    vendor: str = ""


class GatewayConfig(BaseModel):
    """Everything the gateway needs from the host.

    Credentials do not live here: the host resolves each route's vendor key from
    the winnie-llm environment and attaches it when it builds the table.
    """

    model_config = ConfigDict(extra="forbid")

    routes: dict[str, RouteConfig] = Field(default_factory=dict)
    log_root: Path
    anthropic_version: str = "2023-06-01"
