"""Shared tuning for model calls.

Provider choice (supplier, endpoint, model) belongs to the registry; only values
that apply to every provider live here, and the registry stores them so the
console can edit them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0)
    max_output_tokens: int = Field(default=4096, ge=1, le=32768)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0, le=30.0)
    translation_batch_chars: int = Field(default=6000, ge=500, le=50000)
    summary_chunk_chars: int = Field(default=24000, ge=2000, le=100000)
