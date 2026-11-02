"""ASR settings that belong to the vendor adapter, not to a capability."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class VolcengineASRConfig(BaseModel):
    """Doubao speech-to-text endpoint options.

    The API key is not here: it is stored on the supplier record in the
    registry.
    """

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
