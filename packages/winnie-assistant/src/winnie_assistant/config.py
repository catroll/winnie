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


class MemoryConfig(BaseModel):
    """Long-term memory built from conversation files."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # Extra read-only Markdown roots indexed for retrieval (for example
    # ../../personal). Nothing is ever written to them.
    extra_roots: list[str] = Field(default_factory=list)
    # Pinned memory is always in the system prompt; this bounds its size.
    digest_chars: int = Field(default=2000, ge=0, le=20000)
    auto_distill: bool = True
    max_facts_per_run: int = Field(default=12, ge=1, le=100)
    max_fact_chars: int = Field(default=500, ge=50, le=2000)


class TasksConfig(BaseModel):
    """Scheduled task definitions and the in-process scheduler."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    file: str = "tasks.yaml"
    tick_seconds: float = Field(default=30.0, ge=2.0, le=3600.0)
    # A missed run older than this is skipped instead of replayed.
    catch_up_seconds: int = Field(default=3600, ge=0, le=86400)
    webhook_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)


class AssistantConfig(BaseModel):
    """Everything the assistant capability needs from the host."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    root: str = "../../data/assistant"
    timezone: str = "Asia/Shanghai"
    # Empty means: reuse llm.base_url, llm.model and llm.api_key_env.
    model: str = ""
    max_history_messages: int = Field(default=30, ge=2, le=200)
    max_tool_rounds: int = Field(default=4, ge=1, le=10)
    max_tool_output_chars: int = Field(default=4000, ge=200, le=20000)
    search: SearchConfig = Field(default_factory=SearchConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
