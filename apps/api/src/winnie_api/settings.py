"""Host settings: compose framework and capability configuration."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from winnie_assistant.config import AssistantConfig
from winnie_core.config import AuthConfig, JobsConfig, LLMConfig
from winnie_gateway.config import GatewayConfig, RouteConfig
from winnie_meetings.config import MeetingsConfig

_API_ROOT = Path(__file__).resolve().parents[2]


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "sqlite+aiosqlite:///../../data/winnie.db"
    echo: bool = False
    auto_create_tables: bool = True


class IndexConfig(BaseModel):
    """Where the local document index daemon listens, if it is running."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    base_url: str = "http://127.0.0.1:8790"
    timeout_seconds: float = 60.0


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_root: str = "../../data/logs"
    anthropic_version: str = "2023-06-01"
    proxy_api_key: str = ""
    routes: dict[str, RouteConfig] = Field(default_factory=dict)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    meetings: MeetingsConfig = Field(default_factory=MeetingsConfig)
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)

    def gateway_config(self) -> GatewayConfig:
        return GatewayConfig(
            routes=self.routes,
            log_root=self.resolve_log_root(),
            anthropic_version=self.anthropic_version,
            proxy_api_key=self.proxy_api_key,
        )

    def resolve_log_root(self) -> Path:
        return _resolve_path(self.log_root)

    def resolve_meetings_root(self) -> Path:
        return _resolve_path(self.meetings.local_root)

    def resolve_notes_root(self) -> Path:
        return _resolve_path(self.meetings.notes_root)

    def resolve_assistant_root(self) -> Path:
        return _resolve_path(self.assistant.root)

    def resolve_assistant_tasks_file(self) -> Path:
        path = Path(self.assistant.tasks.file)
        if path.is_absolute():
            return path
        return self.resolve_assistant_root() / path

    def resolve_database_url(self) -> str:
        """Make relative SQLite paths absolute; leave server URLs untouched."""
        url = self.database.url
        if not url.startswith("sqlite"):
            return url
        prefix, separator, raw_path = url.partition(":///")
        if not separator or not raw_path or raw_path == ":memory:":
            return url
        path = Path(raw_path)
        if path.is_absolute():
            return url
        return f"{prefix}:///{(_API_ROOT / path).resolve()}"


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
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
    """Load config.yaml and overlay the sibling *.local.yaml when present.

    ``WINNIE_CONFIG`` lets the API, worker and Alembic share one non-default
    config file. An explicit argument still wins.
    """
    primary = config_path or Path(
        os.environ.get("WINNIE_CONFIG", _API_ROOT / "config.yaml")
    )
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
