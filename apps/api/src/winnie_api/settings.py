"""Load application settings from YAML."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from winnie_api.gateway.routes import Route

_API_ROOT = Path(__file__).resolve().parents[2]


class RouteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["openai", "anthropic"]
    base_url: str


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_root: str = "../../data/logs"
    anthropic_version: str = "2023-06-01"
    proxy_api_key: str = ""
    routes: dict[str, RouteConfig] = Field(default_factory=dict)

    def route_table(self) -> dict[str, Route]:
        return {
            name: Route(name=name, protocol=cfg.protocol, base_url=cfg.base_url)
            for name, cfg in self.routes.items()
        }

    def resolve_log_root(self) -> Path:
        path = Path(self.log_root)
        if not path.is_absolute():
            path = (_API_ROOT / path).resolve()
        return path


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config must be a mapping: {path}")
    return data


def load_settings(config_path: Path | None = None) -> Settings:
    """Load config.yaml and overlay the sibling *.local.yaml when present."""
    primary = config_path or (_API_ROOT / "config.yaml")
    if not primary.is_file():
        raise FileNotFoundError(f"missing config file: {primary}")
    data = load_yaml_file(primary)
    local = primary.with_name(f"{primary.stem}.local{primary.suffix}")
    if local.is_file():
        data = _deep_merge(data, load_yaml_file(local))
    return Settings.model_validate(data)


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings for CLI entry points; the app uses app.state."""
    return load_settings()
