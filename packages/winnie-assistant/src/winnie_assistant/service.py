"""Composition root for the assistant capability.

The service owns the long-lived objects (store, memory, agent, scheduler) and
the two background jobs: memory distillation and scheduled tasks.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from winnie_chat import ChatModel, SkillRegistry, SkillView

from winnie_assistant.agent import AssistantAgent
from winnie_assistant.config import AssistantConfig
from winnie_assistant.conversations import ConversationStore
from winnie_assistant.index_client import IndexClient, IndexHit
from winnie_assistant.layout import ensure_layout
from winnie_assistant.memory import MemoryStore
from winnie_assistant.memory_builder import DistillResult, MemoryBuilder
from winnie_assistant.scheduler import (
    AssistantScheduler,
    ScheduledTask,
    TaskRun,
    TaskRunPage,
)
from winnie_assistant.search import SearchProvider
from winnie_assistant.types import ChatReply
from winnie_assistant.weather import WeatherRun, WeatherWatch

logger = logging.getLogger(__name__)


class AssistantService:
    def __init__(
        self,
        *,
        config: AssistantConfig,
        root: Path,
        store: ConversationStore,
        memory: MemoryStore | None,
        builder: MemoryBuilder | None,
        agent: AssistantAgent,
        scheduler: AssistantScheduler,
        registry: SkillRegistry,
        model: ChatModel,
        model_name: str,
        search: SearchProvider,
        index: IndexClient | None,
        weather_watch: WeatherWatch | None,
        timezone: ZoneInfo,
        clock=None,
    ) -> None:
        self.config = config
        self.root = root
        self.store = store
        self.memory = memory
        self.builder = builder
        self.agent = agent
        self.scheduler = scheduler
        self.registry = registry
        self.model = model
        self.model_name = model_name
        self.search = search
        self.index = index
        self.weather_watch = weather_watch
        self.timezone = timezone
        self.clock = clock or (lambda: datetime.now(timezone))
        self._distill_task: asyncio.Task[None] | None = None

    # -- lifecycle -------------------------------------------------------

    async def startup(self) -> None:
        await asyncio.to_thread(ensure_layout, self.root)
        if self.memory is not None:
            await asyncio.to_thread(self.memory.refresh)
        if self.config.tasks.enabled:
            await self.scheduler.start()
        else:
            await asyncio.to_thread(self.scheduler.reload, force=True)
        if self.weather_watch is not None:
            await self.weather_watch.start()
        if self.builder is not None and self.config.memory.auto_distill:
            self._schedule_distill()

    async def shutdown(self) -> None:
        await self.scheduler.stop()
        if self.weather_watch is not None:
            await self.weather_watch.stop()
        if self._distill_task is not None:
            self._distill_task.cancel()
            try:
                await self._distill_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - shutdown must not raise
                logger.exception("memory distillation failed during shutdown")
            self._distill_task = None

    # -- chat ------------------------------------------------------------

    async def chat(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
        source: str = "chat",
        channel: str = "api",
        task_id: str = "",
        title: str | None = None,
    ) -> ChatReply:
        reply = await self.agent.chat(
            text,
            conversation_id=conversation_id,
            source=source,
            channel=channel,
            task_id=task_id,
            title=title,
        )
        if self.builder is not None and self.config.memory.auto_distill:
            self._schedule_distill()
        return reply

    # -- skills ----------------------------------------------------------

    def skills(self) -> list[SkillView]:
        return self.registry.views()

    def set_skill_enabled(self, name: str, enabled: bool) -> SkillView:
        return self.registry.set_enabled(name, enabled)

    # -- weather ---------------------------------------------------------

    async def check_weather(self, *, force: bool = True) -> WeatherRun:
        if self.weather_watch is None:
            raise RuntimeError("weather is disabled")
        return await self.weather_watch.run_now(force=force)

    def weather_status(self) -> dict:
        if self.weather_watch is None:
            return {
                "enabled": False,
                "location": self.config.weather.location,
                "schedule": self.config.weather.schedule,
                "next_run": None,
                "last_run": None,
                "last_status": None,
                "last_conversation_id": None,
                "webhook": bool(self.config.weather.webhook),
            }
        return self.weather_watch.status()

    # -- local documents -------------------------------------------------

    async def search_documents(
        self, query: str, *, limit: int | None = None
    ) -> list[IndexHit]:
        if self.index is None:
            raise RuntimeError("the local index is disabled")
        return await self.index.search(query, limit=limit)

    # -- memory ----------------------------------------------------------

    async def distill(self) -> DistillResult:
        if self.builder is None:
            raise RuntimeError("memory is disabled")
        return await self.builder.run()

    def _schedule_distill(self) -> None:
        if self.builder is None:
            return
        if self._distill_task is not None and not self._distill_task.done():
            return
        self._distill_task = asyncio.create_task(
            self._distill_in_background(), name="assistant-memory"
        )

    async def _distill_in_background(self) -> None:
        try:
            result = await self.builder.run()  # type: ignore[union-attr]
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - memory is best effort
            logger.exception("background memory distillation failed")
            return
        logger.info(
            "memory distilled: conversations=%s facts=%s empty=%s",
            result.conversations,
            result.facts,
            result.empty,
        )

    # -- tasks -----------------------------------------------------------

    async def run_task(self, task_id: str) -> TaskRun:
        return await self.scheduler.run_now(task_id)

    def task_runs(
        self,
        *,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> TaskRunPage:
        """Read the append-only run log; the scheduler owns its path."""
        return self.scheduler.task_runs(
            task_id=task_id, limit=limit, offset=offset
        )

    async def chat_task(self, task: ScheduledTask) -> ChatReply:
        """One scheduled task turn, recorded as a task-sourced conversation."""
        return await self.chat(
            task.prompt,
            source="task",
            channel="task",
            task_id=task.id,
            title=task.title,
        )

    # -- status ----------------------------------------------------------

    def status(self) -> dict:
        memory_entries = len(self.memory.entries) if self.memory is not None else 0
        memory_files = len(self.memory.files()) if self.memory is not None else 0
        return {
            "model": getattr(self.model, "name", self.model_name),
            "search": self.search.name,
            "timezone": self.timezone.key,
            "root": str(self.root),
            "conversations": self.store.count(),
            "skills": len(self.registry.views()),
            "memory": {
                "enabled": self.memory is not None,
                "entries": memory_entries,
                "files": memory_files,
            },
            "index": {
                "enabled": self.index is not None,
                "url": self.index.url if self.index is not None else "",
            },
            "scheduler": {
                "running": self.config.tasks.enabled,
                "tasks": len(self.scheduler.tasks),
                "errors": self.scheduler.errors,
            },
            "weather": self.weather_status(),
        }
