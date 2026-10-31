"""Service layer: owns the store, the event index and run lifecycle.

The HTTP router and the assistant skill both talk to this object; neither touches
files or the pipeline directly. One run may execute at a time: the store writes
are atomic per file but two runs would race on the shared event index, and the
sources are rate-limited politely.

A run started from HTTP returns immediately and continues in the request
process. That is acceptable for a personal deployment and matches the inline job
backend; ``run_now`` exists for tests and CLI use.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from winnie_news.aggregate.models import Event
from winnie_news.aggregate.store import EventStore
from winnie_news.common.timeutil import now_iso
from winnie_news.config import NewsConfig
from winnie_news.generate.models import Draft
from winnie_news.material.fetcher import get_all_materials
from winnie_news.pipeline import STAGES, run_pipeline, write_and_review_event
from winnie_news.ports import LLMPort, SearchPort
from winnie_news.search.models import EventSearch
from winnie_news.search.search import usable_hits
from winnie_news.sources import SourceHealthStore
from winnie_news.store import NewsStore, RunRecord, StageRun, new_run_id

logger = logging.getLogger(__name__)


class NewsError(RuntimeError):
    """Base class for service errors the API maps to a status code."""


class NewsUnavailable(NewsError):
    """The pipeline cannot run: no model is bound or news is disabled."""


class RunBusy(NewsError):
    """Another run is in progress."""


class RunNotFound(NewsError):
    """The requested run does not exist."""


class NewsService:
    def __init__(
        self,
        config: NewsConfig,
        *,
        root: Path | None = None,
        llm: LLMPort | None = None,
        search: SearchPort | None = None,
        material_source: Callable[..., dict[str, Any]] = get_all_materials,
    ) -> None:
        self.config = config
        self.root = Path(root or config.root).expanduser()
        self.store = NewsStore(self.root)
        self.event_store = EventStore()
        self.llm = llm
        self.search = search
        self._material_source = material_source
        self._active: str | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ lifecycle

    async def startup(self) -> None:
        self.store.ensure()
        loaded = self.event_store.load(self.store.events_path)
        logger.info("news: %d events loaded from %s", loaded, self.store.events_path)
        recovered = self.recover_abandoned_runs()
        if recovered:
            logger.warning(
                "news: marked %d run(s) failed because the service restarted "
                "while they were running: %s",
                len(recovered),
                ", ".join(recovered),
            )

    def recover_abandoned_runs(self) -> list[str]:
        """Fail runs left `running` by a previous process.

        A run executes in the request process, so a restart leaves its record
        running for ever and the console shows a run that will never advance.
        Recovery belongs at startup, before anything can start a new run.
        """
        recovered: list[str] = []
        for record in self.store.list_runs(limit=10_000):
            if record.status != "running":
                continue
            record.status = "failed"
            record.error = "the service restarted while this run was in progress"
            self._mark_running_stage_failed(record, record.error)
            self.store.save_run(record)
            recovered.append(record.id)
        return recovered

    async def shutdown(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None
        self._active = None

    # ------------------------------------------------------------ status

    def model_readiness(self) -> dict[str, Any] | None:
        """Ask the model adapter whether every purpose can make a call.

        The package knows no vendor; an adapter that can answer does, and one
        that cannot is treated as ready so a simple test double still works.
        """
        check = getattr(self.llm, "readiness", None)
        if not callable(check):
            return None
        try:
            return check()
        except Exception as exc:  # noqa: BLE001 - a broken check is a failed check
            return {
                "ready": False,
                "features": [],
                "error": f"{type(exc).__name__}: {exc}",
            }

    @property
    def ready(self) -> bool:
        if self.llm is None:
            return False
        readiness = self.model_readiness()
        return bool(readiness.get("ready", False)) if readiness is not None else True

    @property
    def active_run(self) -> str | None:
        return self._active

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "ready": self.ready,
            "model": self.model_readiness(),
            "llm": getattr(self.llm, "name", "") if self.llm is not None else "",
            "search": getattr(self.search, "name", "") if self.search is not None else "",
            "active_run": self._active,
            "events": self.event_store.size,
            "runs": len(self.store.list_runs(limit=1_000)),
            "root": str(self.root),
        }

    # ------------------------------------------------------------ runs

    def create_run(
        self, *, source_types: list[str] | None = None
    ) -> RunRecord:
        if not self.config.enabled:
            raise NewsUnavailable("news is disabled (news.enabled is false)")
        if not self.ready:
            raise NewsUnavailable(_unusable_reason(self.model_readiness()))
        run = RunRecord(
            id=new_run_id(),
            created_at=now_iso(),
            updated_at=now_iso(),
            status="running",
            source_types=list(source_types or self.config.material.source_types),
            stages=[StageRun(name=stage) for stage in STAGES],
        )
        self.store.save_run(run)
        return run

    async def start_run(self, *, source_types: list[str] | None = None) -> RunRecord:
        """Create a run and execute it in the background."""
        if self._active is not None:
            raise RunBusy(f"run {self._active} is already in progress")
        run = self.create_run(source_types=source_types)
        self._active = run.id
        self._task = asyncio.create_task(self._execute(run))
        return run

    async def run_now(self, *, source_types: list[str] | None = None) -> RunRecord:
        """Run synchronously; used by tests and command-line tooling."""
        if self._active is not None:
            raise RunBusy(f"run {self._active} is already in progress")
        run = self.create_run(source_types=source_types)
        self._active = run.id
        return await self._execute(run)

    async def _execute(self, run: RunRecord) -> RunRecord:
        try:
            run.set_stage("material", "running")
            self.store.save_run(run)
            materials = await asyncio.to_thread(self._fetch_materials, run.source_types)
            self._record_source_health(materials)
            await run_pipeline(
                run=run,
                store=self.store,
                event_store=self.event_store,
                llm=self._require_llm(),
                search=self.search,
                materials=materials,
                config=self.config,
            )
        except asyncio.CancelledError:
            run.status = "failed"
            run.error = "run cancelled because the service stopped"
            self._mark_running_stage_failed(run, run.error)
            self.store.save_run(run)
            raise
        except Exception as exc:  # noqa: BLE001 - the run record is the report
            logger.exception("news run %s failed", run.id)
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            self._mark_running_stage_failed(run, run.error)
            self.store.save_run(run)
        finally:
            self._active = None
            self._task = None
        return run

    def _fetch_materials(self, source_types: list[str] | None) -> dict[str, Any]:
        material = self.config.material
        cache_dir = self._resolve(material.cache_dir)
        sources = self._resolve(material.sources_file) if material.sources_file else None
        rss_sources = (
            self._resolve(material.rss_sources_file)
            if material.rss_sources_file
            else None
        )
        return self._material_source(
            cache_dir=cache_dir,
            max_age=material.max_age_seconds,
            source_types=source_types or material.source_types or None,
            config_path=sources,
            rss_config_path=rss_sources,
            rss_feed_ids=material.rss_feed_ids or None,
            rss_limit=material.rss_limit,
            workers=material.workers,
        )

    def _record_source_health(self, materials: dict[str, Any]) -> None:
        sources = materials.get("sources")
        if not isinstance(sources, list):
            return
        try:
            SourceHealthStore(self.root).apply(sources)
        except Exception as exc:  # noqa: BLE001 - health is observability
            logger.warning("could not record source health: %s", exc)

    def source_health(self) -> dict[str, Any]:
        health = SourceHealthStore(self.root).load()
        return {
            "updated_at": health.updated_at,
            "sources": [state.model_dump() for state in health.ordered()],
        }

    def _resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (self.root / path).resolve()
        return path

    def _require_llm(self) -> LLMPort:
        if self.llm is None:
            raise NewsUnavailable("no model is bound for news")
        return self.llm

    @staticmethod
    def _mark_running_stage_failed(run: RunRecord, detail: str) -> None:
        for stage in run.stages:
            if stage.status == "running":
                stage.status = "failed"
                stage.detail = detail
            elif stage.status == "pending":
                # The run stopped before this stage; leaving it pending makes a
                # failed run look like it is still about to advance.
                stage.status = "skipped"
        run.updated_at = now_iso()

    def list_runs(self, *, limit: int = 20) -> list[RunRecord]:
        return self.store.list_runs(limit=limit)

    def get_run(self, run_id: str) -> RunRecord:
        try:
            return self.store.load_run(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise RunNotFound(f"run {run_id!r} does not exist") from exc

    def run_drafts(self, run_id: str) -> list[dict[str, Any]]:
        self.get_run(run_id)
        return self.store.list_drafts(run_id)

    def run_reviews(self, run_id: str) -> list[dict[str, Any]]:
        self.get_run(run_id)
        return self.store.list_reviews(run_id)

    def draft(self, run_id: str, event_id: str) -> tuple[Draft, dict[str, Any] | None]:
        self.get_run(run_id)
        try:
            draft = self.store.load_draft(run_id, event_id)
        except (FileNotFoundError, ValueError) as exc:
            raise RunNotFound(
                f"no draft for event {event_id!r} in run {run_id!r}"
            ) from exc
        return draft, self.store.load_review(run_id, event_id)

    async def regenerate_draft(
        self, run_id: str, event_id: str
    ) -> tuple[Draft, Any]:
        """Rewrite one draft of a finished run and review it again.

        The run's stored search results are reused, so a regeneration costs one
        write/review loop and not a new collection. A running batch is refused:
        both paths write the event store and the drafts directory.
        """
        self.get_run(run_id)
        if self._active is not None:
            raise RunBusy(f"run {self._active} is already in progress")
        event = self.event_store.events.get(event_id)
        if event is None:
            raise RunNotFound(f"event {event_id!r} is not in the event store")
        try:
            previous = self.store.load_draft(run_id, event_id)
            revision = previous.revision + 1
        except (FileNotFoundError, ValueError):
            previous = None
            revision = 1

        requirements = self.config.writing.model_copy(
            update={"language": self.config.language}
        )
        draft, review = await write_and_review_event(
            event,
            self._run_hits(run_id, event_id),
            self._require_llm(),
            self.config,
            requirements,
            revision=revision,
        )
        self.store.save_draft(run_id, draft)
        self.store.save_review(run_id, review)
        event.status = "ready" if review.passed else "needs_review"
        self.event_store.save(self.store.events_path)
        return draft, review

    def _run_hits(self, run_id: str, event_id: str) -> list:
        document = self.store.load_search(run_id)
        for entry in (document or {}).get("events") or []:
            if entry.get("event_id") == event_id:
                try:
                    return usable_hits(EventSearch.model_validate(entry))
                except Exception:  # noqa: BLE001 - stale file, no leads
                    return []
        return []

    def draft_markdown(self, run_id: str, event_id: str) -> str:
        self.get_run(run_id)
        try:
            return self.store.draft_markdown(run_id, event_id)
        except (FileNotFoundError, ValueError) as exc:
            raise RunNotFound(
                f"no draft for event {event_id!r} in run {run_id!r}"
            ) from exc

    # ------------------------------------------------------------ events

    def list_events(self, *, limit: int = 50, status: str = "") -> list[Event]:
        events = sorted(
            self.event_store.events.values(),
            key=lambda event: event.last_seen or event.created_at,
            reverse=True,
        )
        if status:
            events = [event for event in events if event.status == status]
        return events[:limit]

    def get_event(self, event_id: str) -> Event | None:
        return self.event_store.events.get(event_id)

def _unusable_reason(readiness: dict[str, Any] | None) -> str:
    """One sentence naming the concrete fix for an unusable model binding."""
    if not readiness:
        return (
            "no model is bound for news; choose one on the AI providers page "
            "(bindings news.extract, news.write, news.review)"
        )
    errors: list[str] = []
    if readiness.get("error"):
        errors.append(str(readiness["error"]))
    for feature in readiness.get("features") or []:
        error = str(feature.get("error") or "")
        if error and error not in errors:
            errors.append(error)
    if errors:
        return "news cannot run: " + "; ".join(errors)
    return "news has no usable model; check the news bindings in Admin -> AI suppliers"

