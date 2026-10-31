"""Compose a meeting service from explicit host-provided dependencies."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_meetings.integrations.asr.base import ASRProvider
from winnie_meetings.integrations.asr.realtime import RealtimeASRProvider
from winnie_meetings.integrations.factory import (
    build_asr_provider,
    build_realtime_asr_provider,
)
from winnie_meetings.integrations.summarize.base import Summarizer
from winnie_meetings.integrations.summarize.fake import FakeSummarizer
from winnie_meetings.integrations.translate.base import Translator
from winnie_meetings.integrations.translate.fake import FakeTranslator
from winnie_core.jobs.queue import ArqJobQueue, InlineJobQueue
from winnie_knowledge.note import MeetingNoteWriter
from winnie_meetings.service import MeetingService
from winnie_core.storage import LocalStorage
from winnie_core.config import JobsConfig
from winnie_meetings.config import MeetingsConfig


def build_meeting_service(
    *,
    config: MeetingsConfig,
    jobs: JobsConfig,
    session_factory: async_sessionmaker[AsyncSession],
    meetings_root: Path,
    notes_root: Path,
    asr: ASRProvider | None = None,
    realtime_asr: RealtimeASRProvider | None = None,
    translator: Translator | None = None,
    summarizer: Summarizer | None = None,
) -> MeetingService:
    service = MeetingService(
        session_factory=session_factory,
        config=config,
        storage=LocalStorage(meetings_root),
        asr=asr if asr is not None else build_asr_provider(config),
        realtime_asr=(
            realtime_asr
            if realtime_asr is not None
            else build_realtime_asr_provider(config)
        ),
        translator=translator if translator is not None else FakeTranslator(),
        summarizer=summarizer if summarizer is not None else FakeSummarizer(),
        note_writer=MeetingNoteWriter(notes_root),
    )
    if jobs.backend == "inline":
        service.set_queue(InlineJobQueue(service.process))
    else:
        service.set_queue(ArqJobQueue(jobs.redis_url))
    return service
