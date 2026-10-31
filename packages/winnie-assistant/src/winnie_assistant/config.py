"""Assistant configuration, validated by the host's YAML loader."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from winnie_local.config import LocalConfig


class SearchConfig(BaseModel):
    """Web search provider settings."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["bing_rss", "fake"] = "bing_rss"
    endpoint: str = "https://cn.bing.com/search"
    max_results: int = Field(default=5, ge=1, le=20)
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)


class IndexConfig(BaseModel):
    """The local document index that serves the assistant's memory.

    The index owns tokenisation, chunking and freshness; the assistant only
    sends queries and reads documents back. It is a separate loopback daemon
    because the same index serves other agents.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    url: str = "http://127.0.0.1:8790"
    token_env: str = "WINNIE_INDEX_TOKEN"
    timeout_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    limit: int = Field(default=8, ge=1, le=50)


class MemoryConfig(BaseModel):
    """Long-term memory notes, distilled from conversation files."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # Pinned memory is always in the system prompt; this bounds its size.
    digest_chars: int = Field(default=2000, ge=0, le=20000)
    auto_distill: bool = True
    max_facts_per_run: int = Field(default=12, ge=1, le=100)
    max_fact_chars: int = Field(default=500, ge=50, le=2000)


class TasksConfig(BaseModel):
    """Assistant-managed scheduled tasks and the in-process scheduler."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # Relative to the assistant root. The assistant writes this file; do not
    # edit it by hand, ask the assistant to create or cancel a task.
    file: str = "tasks/tasks.json"
    tick_seconds: float = Field(default=30.0, ge=2.0, le=3600.0)
    # A missed run older than this is skipped instead of replayed.
    catch_up_seconds: int = Field(default=3600, ge=0, le=86400)
    webhook_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    # Push the result of a scheduled run to the owner's phone when push is
    # configured. A brief nobody sees is not a brief; turn this off for tasks
    # that are only bookkeeping.
    notify_results: bool = True


class WeatherConfig(BaseModel):
    """The default weather watch: forecast, advisories and a daily notice."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    location: str = "上海"
    latitude: float = 31.2304
    longitude: float = 121.4737
    # Daily check by default; "every 3h" is also valid. Duplicate advisories
    # on the same day are not sent again.
    schedule: str = "daily 07:00"
    webhook: str = ""
    rain_probability: int = Field(default=50, ge=0, le=100)
    temperature_delta: float = Field(default=5.0, ge=1.0, le=20.0)
    heat_threshold: float = Field(default=35.0, ge=20.0, le=50.0)
    cold_threshold: float = Field(default=0.0, ge=-40.0, le=20.0)
    wind_threshold: float = Field(default=39.0, ge=10.0, le=200.0)
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)
    webhook_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    tick_seconds: float = Field(default=30.0, ge=5.0, le=600.0)


class WorkspaceConfig(BaseModel):
    """Local files the assistant may inspect and change.

    The file tools resolve every path against ``root`` and ``extra_roots`` and
    refuse the assistant's own storage and common credential directories. This
    is path confinement for the file tools, not an OS sandbox.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    root: str = "~"
    extra_roots: list[str] = Field(default_factory=list)
    # Extra denied paths. The built-in protected set always applies.
    protected: list[str] = Field(default_factory=list)
    max_read_bytes: int = Field(default=200_000, ge=1_000, le=5_000_000)
    max_write_bytes: int = Field(default=200_000, ge=1_000, le=5_000_000)
    max_list_entries: int = Field(default=500, ge=1, le=5_000)
    # Overwriting or appending to an existing file asks for confirmation.
    confirm_writes: bool = True


class ExecConfig(BaseModel):
    """Local command execution.

    Disabled by default. When enabled, commands run as the API user; the
    confirmation gate and audit log are guardrails, not a sandbox.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    workdir: str = "~"
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    max_output_chars: int = Field(default=8_000, ge=200, le=100_000)
    # Empty means any executable. A non-empty list checks argv[0]'s basename;
    # it is a guardrail, not a security boundary.
    allow: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    require_confirmation: bool = True
    pending_ttl_seconds: int = Field(default=300, ge=30, le=3_600)


class AssistantConfig(BaseModel):
    """Everything the assistant capability needs from the host."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # The assistant's memory and conversation files live with the owner's
    # documents, so the local index service can index them.
    root: str = "~/Documents/Mine/Winnie"
    timezone: str = "Asia/Shanghai"
    max_history_messages: int = Field(default=30, ge=2, le=200)
    max_tool_rounds: int = Field(default=4, ge=1, le=10)
    max_tool_output_chars: int = Field(default=4000, ge=200, le=20000)
    search: SearchConfig = Field(default_factory=SearchConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    exec: ExecConfig = Field(default_factory=ExecConfig)
    local: LocalConfig = Field(default_factory=LocalConfig)
