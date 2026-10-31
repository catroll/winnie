"""Compose the auth context from settings."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_core.auth.service import AuthService
from winnie_core.config import AuthConfig


def build_auth_service(
    config: AuthConfig,
    session_factory: async_sessionmaker[AsyncSession],
) -> AuthService:
    return AuthService(session_factory=session_factory, config=config)
