"""Declarative scheduled tasks and the in-process scheduler.

Definitions live in ``tasks.yaml``, which the user edits. Runtime state (the
next run and the last outcome) lives in ``.state/scheduler.json`` so a restart
does not replay a task, and every execution appends one line to
``tasks/runs.jsonl``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import yaml

from winnie_assistant.agent import ChatReply
from winnie_assistant.files import atomic_write

logger = logging.getLogger(__name__)

TASK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
EVERY = re.compile(r"^every\s+(\d+)\s*([mhd])$")
DAILY = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$")
WEEKLY = re.compile(r"^weekly\s+(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2}):(\d{2})$")
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MIN_INTERVAL_MINUTES = 5


class TaskDefinitionError(ValueError):
    """A task in tasks.yaml is unusable; the rest of the file still loads."""


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
    text = value.strip().lower()
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


class TaskFile:
    """Load and validate ``tasks.yaml`` without failing the whole service."""

    def __init__(self, path: Path, default_timezone: str) -> None:
        self.path = path
        self.default_timezone = default_timezone

    def load(self) -> tuple[ZoneInfo, list[ScheduledTask], list[str]]:
        timezone = _load_timezone(self.default_timezone)
        if not self.path.is_file():
            return timezone, [], []
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            return timezone, [], [f"tasks file is unreadable: {exc}"]
        if not isinstance(raw, dict):
            return timezone, [], ["tasks file must be a mapping"]

        timezone = _load_timezone(str(raw.get("timezone") or self.default_timezone))
        entries = raw.get("tasks") or []
        if not isinstance(entries, list):
            return timezone, [], ["tasks must be a list"]

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
        return timezone, tasks, errors


def _parse_task(entry: Any, index: int) -> ScheduledTask:
    if not isinstance(entry, dict):
        raise TaskDefinitionError(f"task #{index + 1} must be a mapping")
    task_id = str(entry.get("id") or "").strip()
    if not TASK_ID.match(task_id):
        raise TaskDefinitionError(
            f"task #{index + 1} needs a lowercase id matching {TASK_ID.pattern}"
        )
    schedule = str(entry.get("schedule") or "").strip()
    prompt = str(entry.get("prompt") or "").strip()
    if not schedule:
        raise TaskDefinitionError(f"task {task_id} has no schedule")
    if not prompt:
        raise TaskDefinitionError(f"task {task_id} has no prompt")
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
    )


def _load_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise TaskDefinitionError(f"unknown timezone: {name}") from None


class AssistantScheduler:
    def __init__(
        self,
        *,
        runner: Callable[[ScheduledTask], Awaitable[ChatReply]],
        tasks_path: Path,
        state_path: Path,
        runs_path: Path,
        timezone: ZoneInfo,
        tick_seconds: float = 30.0,
        catch_up_seconds: int = 3600,
        webhook_timeout_seconds: float = 10.0,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
        background: bool = True,
    ) -> None:
        self.runner = runner
        self.task_file = TaskFile(tasks_path, timezone.key)
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

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self.reload(force=True)
        self.sync(self.clock())
        if self.background:
            self._loop = asyncio.create_task(self._run_loop(), name="assistant-scheduler")

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
            mtime = self.task_file.path.stat().st_mtime
        except OSError:
            mtime = None
        if not force and mtime == self._mtime:
            return
        self._mtime = mtime
        timezone, tasks, errors = self.task_file.load()
        self.timezone = timezone
        self.tasks = tasks
        self.errors = errors
        for error in errors:
            logger.warning("scheduled task ignored: %s", error)
        self._state = self._load_state()

    def find(self, task_id: str) -> ScheduledTask | None:
        return next((task for task in self.tasks if task.id == task_id), None)

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
