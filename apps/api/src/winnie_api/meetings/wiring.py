"""Compose the meetings context from settings."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_api.integrations.factory import (
    build_asr_provider,
    build_summarizer,
    build_translator,
)
from winnie_api.jobs.queue import ArqJobQueue, InlineJobQueue
from winnie_api.knowledge.meeting_note import MeetingNoteWriter
from winnie_api.meetings.service import MeetingService
from winnie_api.shared.storage import LocalStorage
from winnie_api.settings import Settings


def build_meeting_service(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> MeetingService:
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
    return service
