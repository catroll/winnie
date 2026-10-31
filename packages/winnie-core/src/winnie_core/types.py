"""Provider-neutral types shared by ASR, translation and summarization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioRef:
    """A stored audio object.

    `local_path` is set for local providers; cloud providers should use `key`
    to resolve an object-storage URL through their own adapter.
    """

    key: str
    local_path: Path | None = None
    content_type: str | None = None


@dataclass(frozen=True)
class Usage:
    """One billable or measurable provider call."""

    provider: str
    unit: str
    quantity: float
    cost_micros: int = 0
