"""Per-source health across runs.

A run already reports per-source failures, but only for that run. A feed that
has been dead for a week looks exactly like one that failed once, and a quiet
source that simply produced nothing is indistinguishable from a broken one. This
record keeps the last outcome per source so the console can show it before a
batch empties.

The state is shared by every run under one news root, so it must have a single
writer: the news service owns it, and the material step is where it is updated.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from winnie_news.common.timeutil import now_iso

logger = logging.getLogger(__name__)


class SourceState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    type: str
    id: str
    name: str
    last_status: str = "unknown"
    last_success: str | None = None
    last_error: str = ""
    error_at: str | None = None
    consecutive_failures: int = 0
    last_count: int = 0
    updated_at: str


class SourceHealth(BaseModel):
    model_config = ConfigDict(extra="ignore")

    updated_at: str
    sources: dict[str, SourceState] = Field(default_factory=dict)

    def ordered(self) -> list[SourceState]:
        """Failing sources first: they are the reason to open this view."""
        return sorted(
            self.sources.values(),
            key=lambda state: (state.last_status != "error", state.name.lower()),
        )


class SourceHealthStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "source_health.json"

    def load(self) -> SourceHealth:
        if not self.path.is_file():
            return SourceHealth(updated_at=now_iso())
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return SourceHealth.model_validate(raw)
        except (OSError, ValueError) as exc:
            logger.warning("ignoring unreadable source health %s: %s", self.path, exc)
            return SourceHealth(updated_at=now_iso())

    def apply(self, sources: list[dict[str, Any]], *, now: str | None = None) -> SourceHealth:
        """Merge one material batch's per-source results into the record."""
        health = self.load()
        stamp = now or now_iso()
        for entry in sources:
            source_type = str(entry.get("type") or "")
            source_id = str(entry.get("id") or "")
            if not source_type or not source_id:
                continue
            key = f"{source_type}:{source_id}"
            state = health.sources.get(key) or SourceState(
                key=key,
                type=source_type,
                id=source_id,
                name=str(entry.get("name") or source_id),
                updated_at=stamp,
            )
            state.name = str(entry.get("name") or state.name)
            error = str(entry.get("error") or "")
            state.last_count = int(entry.get("count") or 0)
            state.updated_at = stamp
            if error:
                state.last_status = "error"
                state.last_error = error
                state.error_at = stamp
                state.consecutive_failures += 1
            else:
                state.last_status = "ok"
                state.last_error = ""
                state.error_at = None
                state.consecutive_failures = 0
                state.last_success = stamp
            health.sources[key] = state
        health.updated_at = stamp
        self.save(health)
        return health

    def save(self, health: SourceHealth) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(health.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
