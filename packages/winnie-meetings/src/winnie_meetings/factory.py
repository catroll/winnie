"""Compose a meeting service from explicit host-provided dependencies."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_meetings.integrations.factory import (
    build_asr_provider,
    build_realtime_asr_provider,
    build_summarizer,
    build_translator,
)
from winnie_core.jobs.queue import ArqJobQueue, InlineJobQueue
from winnie_knowledge.note import MeetingNoteWriter
from winnie_meetings.service import MeetingService
from winnie_core.storage import LocalStorage
from winnie_core.config import JobsConfig, LLMConfig
from winnie_meetings.config import MeetingsConfig


def build_meeting_service(
    *,
    config: MeetingsConfig,
    llm: LLMConfig,
    jobs: JobsConfig,
    session_factory: async_sessionmaker[AsyncSession],
    meetings_root: Path,
    notes_root: Path,
) -> MeetingService:
    service = MeetingService(
        session_factory=session_factory,
        config=config,
        llm=llm,
        storage=LocalStorage(meetings_root),
        asr=build_asr_provider(config),
        realtime_asr=build_realtime_asr_provider(config),
        translator=build_translator(config, llm),
        summarizer=build_summarizer(config, llm),
        note_writer=MeetingNoteWriter(notes_root),
    )
    if jobs.backend == "inline":
        service.set_queue(InlineJobQueue(service.process))
    else:
        service.set_queue(ArqJobQueue(jobs.redis_url))
    return service
