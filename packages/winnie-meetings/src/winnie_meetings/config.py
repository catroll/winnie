"""Meeting capability configuration.

The ASR endpoint options belong to the vendor adapter, so they are defined in
``winnie_llm.asr`` and only referenced here. No key is part of meeting config:
a supplier key is stored in the AI registry.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from winnie_llm.asr.config import VolcengineASRConfig


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
    # First-start default for the ASR feature binding; the AI suppliers page
    # owns the binding afterwards.
    asr_provider: str = "fake"
    prompt_version: str = "v1"
    volcengine: VolcengineASRConfig = Field(default_factory=VolcengineASRConfig)
