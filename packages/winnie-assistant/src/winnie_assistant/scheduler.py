"""Assistant-managed scheduled tasks and the in-process scheduler.

Tasks are created and cancelled **through conversation**, so ``tasks.json`` is
written by the assistant, not by hand. The file is still plain JSON on disk:
the record is inspectable, and a restart replays nothing because the next-run
state lives beside it. Every execution appends one line to ``runs.jsonl``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from winnie_assistant.types import ChatReply
from winnie_assistant.files import atomic_write

logger = logging.getLogger(__name__)

TASK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
EVERY = re.compile(r"^every\s+(\d+)\s*([mhd])$")
DAILY = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$")
WEEKLY = re.compile(r"^weekly\s+(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2}):(\d{2})$")
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MIN_INTERVAL_MINUTES = 5

SCHEMA_VERSION = 1


class TaskDefinitionError(ValueError):
    """A task is unusable; the caller reports it instead of writing it."""


@dataclass(frozen=True)
class Schedule:
    kind: str
    minutes: int = 0
    hour: int = 0
    minute: int = 0
    weekday: int = 0

    def next_after(self, after: datetime) -> datetime:
        if self.kind == "every":
            return after + timedelta(minutes=self.minutes)
        if self.kind == "daily":
            candidate = after.replace(
                hour=self.hour, minute=self.minute, second=0, microsecond=0
            )
            if candidate <= after:
                candidate += timedelta(days=1)
            return candidate
        if self.kind == "weekly":
            days = (self.weekday - after.weekday()) % 7
            candidate = (after + timedelta(days=days)).replace(
                hour=self.hour, minute=self.minute, second=0, microsecond=0
            )
            if candidate <= after:
                candidate += timedelta(days=7)
            return candidate
        raise ValueError(f"unknown schedule kind: {self.kind}")


@dataclass(frozen=True)
class ScheduledTask:
    id: str
    title: str
    schedule: str
    prompt: str
    enabled: bool
    webhook: str
    parsed: Schedule
    created_at: str = ""


@dataclass(frozen=True)
class TaskRun:
    task_id: str
    status: str
    started_at: datetime
    finished_at: datetime
    conversation_id: str = ""
    error: str = ""
    delivery_error: str = ""


def parse_schedule(value: str) -> Schedule:
    text = " ".join(value.strip().lower().split())
    match = EVERY.match(text)
    if match:
        count = int(match.group(1))
        minutes = count * {"m": 1, "h": 60, "d": 1440}[match.group(2)]
        if minutes < MIN_INTERVAL_MINUTES:
            raise TaskDefinitionError(
                f"schedule '{value}' is too frequent; the minimum is "
                f"{MIN_INTERVAL_MINUTES} minutes"
            )
        return Schedule(kind="every", minutes=minutes)
    match = DAILY.match(text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        _validate_time(hour, minute, value)
        return Schedule(kind="daily", hour=hour, minute=minute)
    match = WEEKLY.match(text)
    if match:
        hour, minute = int(match.group(2)), int(match.group(3))
        _validate_time(hour, minute, value)
        return Schedule(
            kind="weekly", weekday=WEEKDAYS[match.group(1)], hour=hour, minute=minute
        )
    raise TaskDefinitionError(
        f"unsupported schedule '{value}'; use 'every 15m', 'daily 07:30' "
        "or 'weekly mon 09:00'"
    )


def _validate_time(hour: int, minute: int, value: str) -> None:
    if hour > 23 or minute > 59:
        raise TaskDefinitionError(f"schedule '{value}' is not a valid time")


class TaskStore:
    """Read and write ``tasks/tasks.json``; the assistant owns this file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> tuple[list[ScheduledTask], list[str]]:
        if not self.path.is_file():
            return [], []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return [], [f"tasks file is unreadable: {exc}"]
        if not isinstance(raw, dict):
            return [], ["tasks file must be a JSON object"]
        entries = raw.get("tasks")
        if not isinstance(entries, list):
            return [], ["tasks must be a list"]

        tasks: list[ScheduledTask] = []
        errors: list[str] = []
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            try:
                task = _parse_task(entry, index)
            except TaskDefinitionError as exc:
                errors.append(str(exc))
                continue
            if task.id in seen:
                errors.append(f"duplicate task id: {task.id}")
                continue
            seen.add(task.id)
            tasks.append(task)
        return tasks, errors

    def save(self, tasks: list[ScheduledTask], *, now: datetime) -> None:
        payload = {
            "version": SCHEMA_VERSION,
            "updated_at": now.isoformat(timespec="seconds"),
            "tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "schedule": task.schedule,
                    "prompt": task.prompt,
                    "enabled": task.enabled,
                    "webhook": task.webhook,
                    "created_at": task.created_at,
                }
                for task in tasks
            ],
        }
        atomic_write(
            self.path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    def new_id(self, title: str, existing: list[ScheduledTask]) -> str:
        words = re.findall(r"[a-z0-9]+", title.lower())
        slug = "-".join(words)[:40].strip("-") or "task"
        if len(slug) < 3:
            # A title of only digits (for example "7点") leaves a one-character
            # slug; prefix it so the id stays readable and matchable.
            slug = f"task-{slug}"
        taken = {task.id for task in existing}
        for _ in range(16):
            candidate = f"{slug}-{secrets.token_hex(3)}"
            if candidate not in taken:
                return candidate
        raise TaskDefinitionError("could not allocate a task id")


def _parse_task(entry: Any, index: int) -> ScheduledTask:
    if not isinstance(entry, dict):
        raise TaskDefinitionError(f"task #{index + 1} must be an object")
    task_id = str(entry.get("id") or "").strip()
    if not TASK_ID.match(task_id):
        raise TaskDefinitionError(f"task #{index + 1} has an invalid id")
    schedule = str(entry.get("schedule") or "").strip()
    prompt = str(entry.get("prompt") or "").strip()
    if not schedule or not prompt:
        raise TaskDefinitionError(f"task {task_id} needs a schedule and a prompt")
    webhook = str(entry.get("webhook") or "").strip()
    if webhook and not webhook.startswith(("http://", "https://")):
        raise TaskDefinitionError(f"task {task_id} webhook must be http(s)")
    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise TaskDefinitionError(f"task {task_id} enabled must be true or false")
    return ScheduledTask(
        id=task_id,
        title=str(entry.get("title") or task_id).strip() or task_id,
        schedule=schedule,
        prompt=prompt,
        enabled=enabled,
        webhook=webhook,
        parsed=parse_schedule(schedule),
        created_at=str(entry.get("created_at") or ""),
    )


class AssistantScheduler:
    def __init__(
        self,
        *,
        runner,
        tasks: TaskStore,
        state_path: Path,
        runs_path: Path,
        timezone: ZoneInfo,
        tick_seconds: float = 30.0,
        catch_up_seconds: int = 3600,
        webhook_timeout_seconds: float = 10.0,
        http: httpx.AsyncClient | None = None,
        clock=None,
        background: bool = True,
    ) -> None:
        self.runner = runner
        self.task_store = tasks
        self.state_path = state_path
        self.runs_path = runs_path
        self.timezone = timezone
        self.tick_seconds = tick_seconds
        self.catch_up_seconds = catch_up_seconds
        self.webhook_timeout_seconds = webhook_timeout_seconds
        self.http = http
        self.background = background
        self.clock = clock or (lambda: datetime.now(timezone))
        self.tasks: list[ScheduledTask] = []
        self.errors: list[str] = []
        self._state: dict[str, dict[str, Any]] = {}
        self._mtime: float | None = None
        self._loop: asyncio.Task[None] | None = None
        self._mutation = asyncio.Lock()

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self.reload(force=True)
        self.sync(self.clock())
        if self.background:
            self._loop = asyncio.create_task(
                self._run_loop(), name="assistant-scheduler"
            )

    async def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()
            try:
                await self._loop
            except asyncio.CancelledError:
                pass
            self._loop = None

    def reload(self, *, force: bool = False) -> None:
        try:
            mtime = self.task_store.path.stat().st_mtime
        except OSError:
            mtime = None
        if not force and mtime == self._mtime:
            return
        self._mtime = mtime
        tasks, errors = self.task_store.load()
        self.tasks = tasks
        self.errors = errors
        for error in errors:
            logger.warning("scheduled task ignored: %s", error)
        self._state = self._load_state()

    def find(self, task_id: str) -> ScheduledTask | None:
        return next((task for task in self.tasks if task.id == task_id), None)

    # -- management through conversation ---------------------------------

    async def create_task(
        self,
        *,
        title: str,
        schedule: str,
        prompt: str,
        webhook: str = "",
    ) -> tuple[ScheduledTask, str | None]:
        title = title.strip()
        prompt = prompt.strip()
        if not title:
            raise TaskDefinitionError("the task needs a title")
        if not prompt:
            raise TaskDefinitionError("the task needs a prompt to run")
        webhook = webhook.strip()
        if webhook and not webhook.startswith(("http://", "https://")):
            raise TaskDefinitionError("the webhook must be an http(s) URL")
        parsed = parse_schedule(schedule)
        now = self.clock()
        async with self._mutation:
            tasks, errors = self.task_store.load()
            if errors:
                raise TaskDefinitionError(
                    "the tasks file has errors; refusing to overwrite it: "
                    + "; ".join(errors)
                )
            task = ScheduledTask(
                id=self.task_store.new_id(title, tasks),
                title=title,
                schedule=schedule.strip(),
                prompt=prompt,
                enabled=True,
                webhook=webhook,
                parsed=parsed,
                created_at=now.isoformat(timespec="seconds"),
            )
            tasks.append(task)
            self.task_store.save(tasks, now=now)
            self.reload(force=True)
            self.sync(now)
            return task, self.state_for(task.id).get("next_run")

    async def delete_task(self, task_id: str) -> bool:
        async with self._mutation:
            tasks, errors = self.task_store.load()
            if errors:
                raise TaskDefinitionError(
                    "the tasks file has errors; refusing to overwrite it: "
                    + "; ".join(errors)
                )
            remaining = [task for task in tasks if task.id != task_id]
            if len(remaining) == len(tasks):
                return False
            now = self.clock()
            self.task_store.save(remaining, now=now)
            self.reload(force=True)
            self.sync(now)
            return True

    # -- scheduling ------------------------------------------------------

    def sync(self, now: datetime) -> None:
        known = {task.id for task in self.tasks}
        self._state = {
            task_id: value
            for task_id, value in self._state.items()
            if task_id in known
        }
        for task in self.tasks:
            entry = self._state.setdefault(task.id, {})
            if not task.enabled:
                entry["next_run"] = None
                continue
            if not entry.get("next_run"):
                entry["next_run"] = _iso(task.parsed.next_after(now))
        self._save_state()

    async def tick(self, now: datetime | None = None) -> list[TaskRun]:
        moment = now or self.clock()
        self.reload()
        self.sync(moment)
        runs: list[TaskRun] = []
        for task in self.tasks:
            if not task.enabled:
                continue
            entry = self._state.get(task.id) or {}
            next_run = _parse_iso(entry.get("next_run"))
            if next_run is None or next_run > moment:
                continue
            overdue = (moment - next_run).total_seconds()
            if overdue > self.catch_up_seconds:
                logger.info(
                    "task %s missed its run by %.0fs; skipping to the next one",
                    task.id,
                    overdue,
                )
                self._state[task.id] = {
                    **entry,
                    "next_run": _iso(task.parsed.next_after(moment)),
                    "last_status": "skipped",
                }
                self._save_state()
                continue
            runs.append(await self._execute(task, moment))
            self._state[task.id] = {
                "next_run": _iso(task.parsed.next_after(moment)),
                "last_run": _iso(moment),
                "last_status": runs[-1].status,
                "last_error": runs[-1].error,
            }
            self._save_state()
        return runs

    async def run_now(self, task_id: str, now: datetime | None = None) -> TaskRun:
        task = self.find(task_id)
        if task is None:
            raise KeyError(task_id)
        return await self._execute(task, now or self.clock())

    # -- execution -------------------------------------------------------

    async def _execute(self, task: ScheduledTask, now: datetime) -> TaskRun:
        conversation_id = ""
        error = ""
        delivery_error = ""
        status = "ok"
        try:
            reply = await self.runner(task)
            conversation_id = reply.conversation_id
            delivery_error = await self._deliver(task, reply)
        except Exception as exc:  # noqa: BLE001 - a failed run is recorded, not raised
            logger.exception("scheduled task %s failed", task.id)
            status = "error"
            error = f"{type(exc).__name__}: {exc}"[:500]
        finished = self.clock()
        record = TaskRun(
            task_id=task.id,
            status=status,
            started_at=now,
            finished_at=finished,
            conversation_id=conversation_id,
            error=error,
            delivery_error=delivery_error,
        )
        self._append_run(record)
        return record

    async def _deliver(self, task: ScheduledTask, reply: ChatReply) -> str:
        if not task.webhook:
            return ""
        client = self.http
        if client is None:
            return "no HTTP client configured for webhook delivery"
        try:
            response = await client.post(
                task.webhook,
                json={
                    "task_id": task.id,
                    "title": task.title,
                    "text": reply.text,
                    "conversation_id": reply.conversation_id,
                },
                timeout=self.webhook_timeout_seconds,
            )
            if response.status_code >= 400:
                return f"webhook returned HTTP {response.status_code}"
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return f"webhook failed: {type(exc).__name__}"
        return ""

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive one bad tick
                logger.exception("scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)

    # -- state -----------------------------------------------------------

    def _load_state(self) -> dict[str, dict[str, Any]]:
        if not self.state_path.is_file():
            return {}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("scheduler state is unreadable; starting fresh")
            return {}
        tasks = raw.get("tasks") if isinstance(raw, dict) else None
        if not isinstance(tasks, dict):
            return {}
        return {
            str(task_id): dict(value)
            for task_id, value in tasks.items()
            if isinstance(value, dict)
        }

    def _save_state(self) -> None:
        payload = {
            "tasks": self._state,
            "updated_at": self.clock().isoformat(timespec="seconds"),
        }
        atomic_write(
            self.state_path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def state_for(self, task_id: str) -> dict[str, Any]:
        return dict(self._state.get(task_id) or {})

    def _append_run(self, record: TaskRun) -> None:
        self.runs_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "task_id": record.task_id,
                "status": record.status,
                "started_at": _iso(record.started_at),
                "finished_at": _iso(record.finished_at),
                "conversation_id": record.conversation_id,
                "error": record.error,
                "delivery_error": record.delivery_error,
            },
            ensure_ascii=False,
        )
        with self.runs_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
