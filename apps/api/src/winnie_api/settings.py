"""Host settings: compose framework and capability configuration."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from winnie_assistant.config import AssistantConfig
from winnie_core.config import AuthConfig, JobsConfig
from winnie_core.usage.config import UsageConfig
from winnie_gateway.config import GatewayConfig, RouteConfig
from winnie_mail.config import MailConfig
from winnie_meetings.config import MeetingsConfig
from winnie_news.config import NewsConfig
from winnie_phone.config import PhoneConfig
from winnie_proactive.config import ProactiveConfig
from winnie_push.config import PushConfig

from winnie_api.devloop.config import DevloopConfig

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


class ScheduleConfig(BaseModel):
    """The owner's weekly schedule tree (WLB ``work/``).

    Disabled by default: the tree is outside this repository, and a test or an
    embedder should never read one by accident. ``root`` may be absolute or
    start with ``~``, which is the normal case for this checkout.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    root: str = "~/Projects/Mine/WLB"


class AIConfig(BaseModel):
    """The admin-managed AI registry and feature bindings.

    Disabled by default so a programmatic ``Settings()`` (tests, embedders)
    never reads or writes a file. ``config.yaml`` turns it on for the service.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Relative paths are resolved against apps/api/, like every other root.
    path: str = "../../data/ai.yaml"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_root: str = "../../data/logs"
    anthropic_version: str = "2023-06-01"
    gateway_keys_path: str = "../../data/gateway-keys.yaml"
    routes: dict[str, RouteConfig] = Field(default_factory=dict)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    meetings: MeetingsConfig = Field(default_factory=MeetingsConfig)
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    phone: PhoneConfig = Field(default_factory=PhoneConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    push: PushConfig = Field(default_factory=PushConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    usage: UsageConfig = Field(default_factory=UsageConfig)
    mail: MailConfig = Field(default_factory=MailConfig)
    devloop: DevloopConfig = Field(default_factory=DevloopConfig)

    def gateway_config(self) -> GatewayConfig:
        return GatewayConfig(
            routes=self.routes,
            log_root=self.resolve_log_root(),
            anthropic_version=self.anthropic_version,
        )

    def resolve_log_root(self) -> Path:
        return _resolve_path(self.log_root)

    def resolve_gateway_keys_path(self) -> Path:
        return _resolve_path(self.gateway_keys_path)

    def resolve_meetings_root(self) -> Path:
        return _resolve_path(self.meetings.local_root)

    def resolve_notes_root(self) -> Path:
        return _resolve_path(self.meetings.notes_root)

    def resolve_assistant_root(self) -> Path:
        return _resolve_path(self.assistant.root)

    def resolve_news_root(self) -> Path:
        return _resolve_path(self.news.root)

    def resolve_assistant_tasks_file(self) -> Path:
        path = Path(self.assistant.tasks.file)
        if path.is_absolute():
            return path
        return self.resolve_assistant_root() / path

    def resolve_ai_path(self) -> Path:
        return _resolve_path(self.ai.path)

    def resolve_usage_root(self) -> Path:
        return _resolve_path(self.usage.root)

    def resolve_mail_root(self) -> Path:
        return _resolve_path(self.mail.root)

    def resolve_schedule_root(self) -> Path:
        return _resolve_path(self.schedule.root)

    def resolve_proactive_root(self) -> Path:
        return _resolve_path(self.proactive.root)

    def resolve_devloop_log_dir(self) -> Path:
        return _resolve_path(self.devloop.log_dir)

    def resolve_devloop_spool_dir(self) -> Path:
        return _resolve_path(self.devloop.spool_dir)

    def resolve_repo_root(self) -> Path:
        """The checkout root, for read-only evidence (git state, log tails)."""
        return _API_ROOT.parents[1]

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


_LIVE_DATA_ROOT = (_API_ROOT / "../../data").resolve()


def refuse_live_database_under_pytest(url: str) -> None:
    """Fail loud when a test would open the owner's real database.

    A fixture that builds ``Settings()`` without ``database=`` gets
    ``data/winnie.db``. ``test_phone_geocode`` did exactly that and synced a fake
    Pixel location into the owner's phone data; the row outlived the test and
    then looked like a real fix. A comment in one fixture does not stop the next
    one, so the engine factory refuses instead, and the message names the fix.
    Called from ``build_runtime``; inert outside a pytest process.
    """
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if not url.startswith("sqlite"):
        return
    _, separator, raw_path = url.partition(":///")
    if not separator or not raw_path or raw_path == ":memory:":
        return
    path = Path(raw_path).resolve()
    if path.parent == _LIVE_DATA_ROOT:
        raise RuntimeError(
            f"a test would open the live database {path}; "
            "give the fixture database=DatabaseConfig(url=...) under tmp_path"
        )


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
