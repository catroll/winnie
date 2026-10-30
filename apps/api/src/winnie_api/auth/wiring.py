"""Compose the auth context from settings."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_api.auth.service import AuthService
from winnie_api.settings import Settings


def build_auth_service(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> AuthService:
    return AuthService(session_factory=session_factory, settings=settings)
