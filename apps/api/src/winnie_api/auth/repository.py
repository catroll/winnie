"""Database access for accounts and sessions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from winnie_api.auth.errors import UsernameTaken
from winnie_api.auth.models import AuthSession, User


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count_users(self) -> int:
        total = await self.session.scalar(select(func.count()).select_from(User))
        return int(total or 0)

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_user_by_username(self, username: str) -> User | None:
        return await self.session.scalar(
            select(User).where(User.username == username)
        )

    async def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str | None,
    ) -> User:
        user = User(
            username=username,
            password_hash=password_hash,
            display_name=display_name,
        )
        self.session.add(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise UsernameTaken(username) from exc
        await self.session.refresh(user)
        return user

    async def update_password_hash(
        self,
        user_id: uuid.UUID,
        password_hash: str,
    ) -> None:
        user = await self.session.get(User, user_id)
        if user is None:
            return
        user.password_hash = password_hash
        await self.session.commit()

    async def create_session(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> AuthSession:
        auth_session = AuthSession(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self.session.add(auth_session)
        await self.session.commit()
        await self.session.refresh(auth_session)
        return auth_session

    async def get_session_with_user(
        self,
        token_hash: str,
    ) -> tuple[AuthSession, User] | None:
        statement = (
            select(AuthSession, User)
            .join(User, AuthSession.user_id == User.id)
            .where(AuthSession.token_hash == token_hash)
        )
        row = (await self.session.execute(statement)).first()
        if row is None:
            return None
        return row[0], row[1]

    async def touch_session(self, session_id: uuid.UUID, when: datetime) -> None:
        auth_session = await self.session.get(AuthSession, session_id)
        if auth_session is None:
            return
        auth_session.last_used_at = when
        await self.session.commit()

    async def delete_session(self, session_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(AuthSession).where(AuthSession.id == session_id)
        )
        await self.session.commit()

    async def delete_session_by_hash(self, token_hash: str) -> None:
        await self.session.execute(
            delete(AuthSession).where(AuthSession.token_hash == token_hash)
        )
        await self.session.commit()

    async def delete_sessions_for_user(self, user_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(AuthSession).where(AuthSession.user_id == user_id)
        )
        await self.session.commit()

    async def delete_expired_sessions(self, before: datetime) -> int:
        result = await self.session.execute(
            delete(AuthSession).where(AuthSession.expires_at < before)
        )
        await self.session.commit()
        return int(result.rowcount or 0)
