"""Configuration objects owned by the framework package."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JobsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["inline", "arq"] = "inline"
    redis_url: str = ""


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    allow_registration: bool = False
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    login_max_attempts: int = Field(default=5, ge=1, le=100)
    login_window_seconds: int = Field(default=300, ge=10, le=86400)
    login_lockout_seconds: int = Field(default=900, ge=10, le=604800)


class LLMConfig(BaseModel):
    """Internal OpenAI-compatible model access for capability jobs."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = ""
    api_key_env: str = "WINNIE_LLM_API_KEY"
    model: str = "fake"
    timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0)
    max_output_tokens: int = Field(default=4096, ge=1, le=32768)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0, le=30.0)
    translation_batch_chars: int = Field(default=6000, ge=500, le=50000)
    summary_chunk_chars: int = Field(default=24000, ge=2000, le=100000)
