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


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "sqlite+aiosqlite:///../../data/winnie.db"
    echo: bool = False
    auto_create_tables: bool = True


class JobsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["inline", "arq"] = "inline"
    redis_url: str = ""


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    allow_registration: bool = False
    session_ttl_days: int = Field(default=30, ge=1, le=365)


class VolcengineASRConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://openspeech.bytedance.com"
    resource_id: str = "volc.bigasr.auc"
    model_name: str = "bigmodel"
    max_inline_mb: int = Field(default=100, ge=1, le=500)
    poll_interval_seconds: float = Field(default=2.0, ge=0.0, le=60.0)
    poll_timeout_seconds: float = Field(default=300.0, ge=1.0, le=3600.0)


class LLMConfig(BaseModel):
    """Internal OpenAI-compatible model access for translation and minutes.

    This is deliberately separate from the gateway routes: internal jobs call
    the provider directly instead of looping back through the proxy.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str = ""
    api_key_env: str = "WINNIE_LLM_API_KEY"
    model: str = "fake"
    timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0)
    max_output_tokens: int = Field(default=4096, ge=1, le=32768)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    translation_batch_chars: int = Field(default=6000, ge=500, le=50000)


class MeetingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    default_user_id: str = "local"
    local_language: str = "zh"
    storage: Literal["local"] = "local"
    local_root: str = "../../data/meetings"
    notes_root: str = "../../data/kb/meetings"
    max_upload_mb: int = 500
    asr_provider: str = "fake"
    translator: str = "fake"
    summarizer: str = "fake"
    prompt_version: str = "v1"
    volcengine: VolcengineASRConfig = Field(default_factory=VolcengineASRConfig)


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

    def route_table(self) -> dict[str, Route]:
        return {
            name: Route(name=name, protocol=cfg.protocol, base_url=cfg.base_url)
            for name, cfg in self.routes.items()
        }

    def resolve_log_root(self) -> Path:
        return _resolve_path(self.log_root)

    def resolve_meetings_root(self) -> Path:
        return _resolve_path(self.meetings.local_root)

    def resolve_notes_root(self) -> Path:
        return _resolve_path(self.meetings.notes_root)

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
    path = Path(value)
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
