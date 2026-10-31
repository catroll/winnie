"""Assistant configuration, validated by the host's YAML loader."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class AssistantConfig(BaseModel):
    """Everything the assistant capability needs from the host."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # The assistant's memory and conversation files live with the owner's
    # documents, so the local index service can index them.
    root: str = "~/Documents/Mine/Winnie"
    timezone: str = "Asia/Shanghai"
    # Empty means: reuse llm.base_url, llm.model and llm.api_key_env.
    model: str = ""
    max_history_messages: int = Field(default=30, ge=2, le=200)
    max_tool_rounds: int = Field(default=4, ge=1, le=10)
    max_tool_output_chars: int = Field(default=4000, ge=200, le=20000)
    search: SearchConfig = Field(default_factory=SearchConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
