"""Compose the meetings context from settings."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from winnie_api.integrations.factory import (
    build_asr_provider,
    build_summarizer,
    build_translator,
)
from winnie_api.jobs.queue import ArqJobQueue, InlineJobQueue
from winnie_api.knowledge.meeting_note import MeetingNoteWriter
from winnie_api.meetings import models as _models  # noqa: F401  (register metadata)
from winnie_api.meetings.service import MeetingService
from winnie_api.shared.db import create_all, create_engine, create_session_factory
from winnie_api.shared.storage import LocalStorage
from winnie_api.settings import Settings


@dataclass
class MeetingRuntime:
    engine: AsyncEngine
    service: MeetingService
    settings: Settings

    async def startup(self) -> None:
        if self.settings.database.auto_create_tables:
            await create_all(self.engine)
        await asyncio.to_thread(
            self.settings.resolve_meetings_root().mkdir,
            parents=True,
            exist_ok=True,
        )
        await asyncio.to_thread(
            self.settings.resolve_notes_root().mkdir,
            parents=True,
            exist_ok=True,
        )

    async def shutdown(self) -> None:
        await self.engine.dispose()


def build_meeting_runtime(settings: Settings) -> MeetingRuntime:
    engine = create_engine(
        settings.resolve_database_url(),
        echo=settings.database.echo,
    )
    session_factory: async_sessionmaker[AsyncSession] = create_session_factory(engine)
    service = MeetingService(
        session_factory=session_factory,
        settings=settings,
        storage=LocalStorage(settings.resolve_meetings_root()),
        asr=build_asr_provider(settings),
        translator=build_translator(settings),
        summarizer=build_summarizer(settings),
        note_writer=MeetingNoteWriter(settings.resolve_notes_root()),
    )
    if settings.jobs.backend == "inline":
        service.set_queue(InlineJobQueue(service.process))
    else:
        service.set_queue(ArqJobQueue(settings.jobs.redis_url))
    return MeetingRuntime(engine=engine, service=service, settings=settings)
