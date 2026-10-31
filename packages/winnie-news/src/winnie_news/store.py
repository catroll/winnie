"""Run records and the on-disk store.

A run is a directory. Everything one batch produced lives under it, so a run can
be inspected, replayed from its material file, or deleted without touching the
long-lived event store::

    <root>/events.json                 # events across runs
    <root>/runs/<run_id>/run.json      # status, stages and stats
    <root>/runs/<run_id>/materials.json
    <root>/runs/<run_id>/clean.json
    <root>/runs/<run_id>/search.json
    <root>/runs/<run_id>/events.json   # this run's event batch
    <root>/runs/<run_id>/drafts/<event_id>.json + .md
    <root>/runs/<run_id>/reviews/<event_id>.json

JSON is the machine format and Markdown is the human one. The draft Markdown is
the artifact a person reads, edits and publishes; it is a derived render of the
JSON, never the source of truth, so a re-review does not have to parse prose.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from winnie_news.common.timeutil import now_iso
from winnie_news.generate.generator import draft_to_markdown
from winnie_news.generate.models import Draft

logger = logging.getLogger(__name__)

RunStatus = Literal["running", "done", "partial", "failed"]
StageStatus = Literal["pending", "running", "ok", "failed", "skipped"]

_RUN_ID_RE = re.compile(r"^[a-z0-9-]{8,64}$")


class StageRun(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    status: StageStatus = "pending"
    started_at: str | None = None
    finished_at: str | None = None
    count: int | None = None
    detail: str = ""


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    created_at: str
    updated_at: str
    status: RunStatus = "running"
    source_types: list[str] = Field(default_factory=list)
    stages: list[StageRun] = Field(default_factory=list)
    stats: dict[str, int | float | str] = Field(default_factory=dict)
    error: str = ""

    def stage(self, name: str) -> StageRun:
        for stage in self.stages:
            if stage.name == name:
                return stage
        stage = StageRun(name=name)
        self.stages.append(stage)
        return stage

    def set_stage(
        self,
        name: str,
        status: StageStatus,
        *,
        count: int | None = None,
        detail: str = "",
    ) -> None:
        stage = self.stage(name)
        stage.status = status
        stage.count = count if count is not None else stage.count
        if detail:
            stage.detail = detail
        stamp = _utcnow()
        if status == "running":
            stage.started_at = stage.started_at or stamp
        elif status in ("ok", "failed", "skipped"):
            stage.finished_at = stamp
        self.updated_at = stamp


class NewsStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser()
        self.runs_root = self.root / "runs"

    # ------------------------------------------------------------ paths

    def ensure(self) -> None:
        self.runs_root.mkdir(parents=True, exist_ok=True)

    @property
    def events_path(self) -> Path:
        return self.root / "events.json"

    def run_dir(self, run_id: str) -> Path:
        if not _RUN_ID_RE.fullmatch(run_id):
            raise ValueError(f"invalid run id {run_id!r}")
        return self.runs_root / run_id

    # ------------------------------------------------------------ runs

    def save_run(self, record: RunRecord) -> None:
        self.ensure()
        directory = self.run_dir(record.id)
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / "run.json", record.model_dump())

    def load_run(self, run_id: str) -> RunRecord:
        path = self.run_dir(run_id) / "run.json"
        if not path.is_file():
            raise FileNotFoundError(f"run {run_id!r} does not exist")
        return RunRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def list_runs(self, *, limit: int = 20) -> list[RunRecord]:
        if not self.runs_root.is_dir():
            return []
        records: list[RunRecord] = []
        for directory in self.runs_root.iterdir():
            if not directory.is_dir():
                continue
            path = directory / "run.json"
            if not path.is_file():
                continue
            try:
                records.append(
                    RunRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
                )
            except Exception as exc:  # noqa: BLE001 - one bad run must not hide others
                logger.warning("skipping unreadable run %s: %s", directory.name, exc)
        records.sort(key=lambda record: record.created_at, reverse=True)
        return records[:limit]

    # ------------------------------------------------------------ stage files

    def save_materials(self, run_id: str, batch: dict[str, Any]) -> None:
        self._write_stage(run_id, "materials.json", batch)

    def load_materials(self, run_id: str) -> dict[str, Any] | None:
        return self._read_stage(run_id, "materials.json")

    def save_clean(self, run_id: str, batch: Any) -> None:
        self._write_stage(run_id, "clean.json", batch.as_document())

    def load_clean(self, run_id: str) -> dict[str, Any] | None:
        return self._read_stage(run_id, "clean.json")

    def save_search(self, run_id: str, batch: Any) -> None:
        self._write_stage(run_id, "search.json", batch.as_document())

    def load_search(self, run_id: str) -> dict[str, Any] | None:
        return self._read_stage(run_id, "search.json")

    def save_event_batch(self, run_id: str, batch: Any) -> None:
        self._write_stage(run_id, "events.json", batch.as_document())

    def load_event_batch(self, run_id: str) -> dict[str, Any] | None:
        return self._read_stage(run_id, "events.json")

    # ------------------------------------------------------------ drafts

    def save_draft(self, run_id: str, draft: Draft) -> Path:
        directory = self.run_dir(run_id) / "drafts"
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / f"{draft.event_id}.json", draft.as_document())
        markdown = directory / f"{draft.event_id}.md"
        _write_text(markdown, draft_to_markdown(draft))
        return markdown

    def load_draft(self, run_id: str, event_id: str) -> Draft:
        path = self.run_dir(run_id) / "drafts" / f"{event_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"no draft for {event_id!r} in run {run_id!r}")
        return Draft.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def draft_markdown(self, run_id: str, event_id: str) -> str:
        path = self.run_dir(run_id) / "drafts" / f"{event_id}.md"
        if not path.is_file():
            raise FileNotFoundError(f"no draft markdown for {event_id!r}")
        return path.read_text(encoding="utf-8")

    def list_drafts(self, run_id: str) -> list[dict[str, Any]]:
        directory = self.run_dir(run_id) / "drafts"
        if not directory.is_dir():
            return []
        drafts: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                draft = Draft.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001
                logger.warning("skipping unreadable draft %s: %s", path, exc)
                continue
            drafts.append(
                {
                    "event_id": draft.event_id,
                    "title": draft.title,
                    "revision": draft.revision,
                    "sources": len(draft.sources),
                    "uncertainties": len(draft.uncertainties),
                    "generated_at": draft.generated_at,
                }
            )
        return drafts

    def save_review(self, run_id: str, review: Any) -> None:
        directory = self.run_dir(run_id) / "reviews"
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / f"{review.event_id}.json", review.as_document())

    def load_review(self, run_id: str, event_id: str) -> dict[str, Any] | None:
        path = self.run_dir(run_id) / "reviews" / f"{event_id}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def list_reviews(self, run_id: str) -> list[dict[str, Any]]:
        directory = self.run_dir(run_id) / "reviews"
        if not directory.is_dir():
            return []
        reviews: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                reviews.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return reviews

    def prune(self, *, keep: int) -> list[str]:
        """Delete all but the newest ``keep`` runs; returns the removed ids."""
        records = self.list_runs(limit=10_000)
        removed: list[str] = []
        for record in records[keep:]:
            directory = self.run_dir(record.id)
            # run_dir validates the id and joins it under runs_root, so this
            # cannot escape the store; use rmtree so nested draft dirs go too.
            if directory.parent != self.runs_root:
                continue
            shutil.rmtree(directory, ignore_errors=True)
            removed.append(record.id)
        return removed

    # ------------------------------------------------------------ helpers

    def _write_stage(self, run_id: str, name: str, document: Any) -> None:
        directory = self.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / name, document)

    def _read_stage(self, run_id: str, name: str) -> dict[str, Any] | None:
        path = self.run_dir(run_id) / name
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("unreadable %s: %s", path, exc)
            return None
        return data if isinstance(data, dict) else None


def _write_json(path: Path, document: Any) -> None:
    text = json.dumps(document, ensure_ascii=False, indent=2)
    _write_text(path, text)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    from uuid import uuid4

    return f"{stamp}-{uuid4().hex[:4]}"


def _utcnow() -> str:
    # Same clock and timezone as the stage documents; a run timeline that mixes
    # UTC and Asia/Shanghai is unreadable in the console.
    return now_iso()
