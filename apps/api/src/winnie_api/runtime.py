"""Host composition for capabilities that need a database."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from winnie_core.auth.models import AuthSession, LoginThrottle, User  # noqa: F401
from winnie_core.auth.service import AuthService
from winnie_core.db import create_all, create_engine, create_session_factory
from winnie_core.usage.ledger import UsageSink
from winnie_meetings.factory import build_meeting_service
from winnie_meetings.models import Meeting  # noqa: F401  (metadata)
from winnie_meetings.service import MeetingService

from winnie_api.ai.providers import (
    DynamicASRProvider,
    DynamicRealtimeASR,
    DynamicSummarizer,
    DynamicTranslator,
)
from winnie_api.ai.store import AISettingsService
from winnie_api.settings import Settings


@dataclass
class AppRuntime:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    auth_service: AuthService | None
    meeting_service: MeetingService | None

    async def startup(self) -> None:
        if self.settings.database.auto_create_tables:
            await create_all(self.engine)
        if self.meeting_service is not None:
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


def build_runtime(
    settings: Settings,
    *,
    ai: AISettingsService | None = None,
    http: httpx.AsyncClient | None = None,
    usage: UsageSink | None = None,
) -> AppRuntime | None:
    if not (settings.auth.enabled or settings.meetings.enabled):
        return None

    engine = create_engine(
        settings.resolve_database_url(),
        echo=settings.database.echo,
    )
    session_factory = create_session_factory(engine)
    auth_service = (
        AuthService(
            session_factory=session_factory,
            config=settings.auth,
        )
        if settings.auth.enabled
        else None
    )
    meeting_service = (
        build_meeting_service(
            config=settings.meetings,
            llm=settings.llm,
            jobs=settings.jobs,
            session_factory=session_factory,
            meetings_root=settings.resolve_meetings_root(),
            notes_root=settings.resolve_notes_root(),
            asr=DynamicASRProvider(settings, ai, http) if ai is not None else None,
            realtime_asr=DynamicRealtimeASR(settings, ai) if ai is not None else None,
            translator=(
                DynamicTranslator(settings, ai, http, usage)
                if ai is not None
                else None
            ),
            summarizer=(
                DynamicSummarizer(settings, ai, http, usage)
                if ai is not None
                else None
            ),
            usage=usage,
        )
        if settings.meetings.enabled
        else None
    )
    return AppRuntime(
        engine=engine,
        session_factory=session_factory,
        settings=settings,
        auth_service=auth_service,
        meeting_service=meeting_service,
    )
