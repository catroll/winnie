"""Meeting capability configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VolcengineASRConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://openspeech.bytedance.com"
    resource_id: str = "volc.seedasr.auc"
    # Streaming uses a different product line and WebSocket endpoint.
    stream_endpoint: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
    stream_resource_id: str = "volc.seedasr.sauc.duration"
    model_name: str = "bigmodel"
    max_inline_mb: int = Field(default=100, ge=1, le=500)
    poll_interval_seconds: float = Field(default=2.0, ge=0.0, le=60.0)
    poll_timeout_seconds: float = Field(default=300.0, ge=1.0, le=3600.0)


class MeetingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    default_user_id: str = "local"
    local_language: str = "zh"
    storage: Literal["local"] = "local"
    local_root: str = "../../data/meetings"
    notes_root: str = "../../data/kb/meetings"
    max_upload_mb: int = 500
    audio_retention_days: int = Field(default=0, ge=0, le=3650)
    asr_provider: str = "fake"
    translator: str = "fake"
    summarizer: str = "fake"
    prompt_version: str = "v1"
    volcengine: VolcengineASRConfig = Field(default_factory=VolcengineASRConfig)
