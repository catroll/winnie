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
