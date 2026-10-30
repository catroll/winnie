"""Application runtime shared by auth and meetings.

Both contexts use the same database engine and session factory. The runtime is
only created when at least one of them is enabled, so the gateway can still run
without a database.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from winnie_api.auth import models as _auth_models  # noqa: F401  (metadata)
from winnie_api.auth.service import AuthService
from winnie_api.auth.wiring import build_auth_service
from winnie_api.meetings import models as _meeting_models  # noqa: F401
from winnie_api.meetings.service import MeetingService
from winnie_api.meetings.wiring import build_meeting_service
from winnie_api.shared.db import create_all, create_engine, create_session_factory
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


def build_runtime(settings: Settings) -> AppRuntime | None:
    if not (settings.auth.enabled or settings.meetings.enabled):
        return None

    engine = create_engine(
        settings.resolve_database_url(),
        echo=settings.database.echo,
    )
    session_factory = create_session_factory(engine)
    auth_service = (
        build_auth_service(settings, session_factory)
        if settings.auth.enabled
        else None
    )
    meeting_service = (
        build_meeting_service(settings, session_factory)
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
