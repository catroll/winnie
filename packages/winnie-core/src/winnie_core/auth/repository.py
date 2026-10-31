"""Database access for accounts and sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from winnie_core.auth.errors import UsernameTaken
from winnie_core.auth.models import AuthSession, LoginThrottle, User
from winnie_core.auth.roles import ROLE_ADMIN, ROLE_USER


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
        role: str = ROLE_USER,
    ) -> User:
        user = User(
            username=username,
            password_hash=password_hash,
            display_name=display_name,
            role=role,
        )
        self.session.add(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise UsernameTaken(username) from exc
        await self.session.refresh(user)
        return user

    async def save_user(self, user: User) -> User:
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def lock_active_admins(self) -> None:
        """Serialize administrator demotions on databases that support row locks.

        PostgreSQL (the production database) blocks a second demotion until the
        first transaction commits, so two concurrent requests cannot each see
        "one other administrator" and leave none. SQLite ignores ``FOR UPDATE``;
        its writes are still serialized, and the guard is re-evaluated inside the
        transaction that performs the update.
        """
        await self.session.execute(
            select(User.id)
            .where(User.role == ROLE_ADMIN, User.is_active.is_(True))
            .with_for_update()
        )

    async def count_active_admins(
        self,
        *,
        exclude_user_id: uuid.UUID | None = None,
    ) -> int:
        statement = (
            select(func.count())
            .select_from(User)
            .where(User.role == ROLE_ADMIN, User.is_active.is_(True))
        )
        if exclude_user_id is not None:
            statement = statement.where(User.id != exclude_user_id)
        total = await self.session.scalar(statement)
        return int(total or 0)

    async def list_user_summaries(
        self,
        *,
        query: str = "",
        role: str | None = None,
        is_active: bool | None = None,
        now: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[User, datetime | None, int]], int]:
        filters = _user_filters(query=query, role=role, is_active=is_active)
        total = await self.session.scalar(
            select(func.count()).select_from(User).where(*filters)
        )
        rows = (
            await self.session.execute(
                _summary_statement(now)
                .where(*filters)
                .order_by(User.created_at.asc(), User.username.asc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        summaries = [(row[0], row[1], int(row[2] or 0)) for row in rows]
        return summaries, int(total or 0)

    async def get_user_summary(
        self,
        user_id: uuid.UUID,
        *,
        now: datetime,
    ) -> tuple[User, datetime | None, int] | None:
        row = (
            await self.session.execute(
                _summary_statement(now).where(User.id == user_id)
            )
        ).first()
        if row is None:
            return None
        return row[0], row[1], int(row[2] or 0)

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

    async def get_login_throttle(self, key: str) -> LoginThrottle | None:
        return await self.session.get(LoginThrottle, key)

    async def record_login_failure(
        self,
        key: str,
        *,
        now: datetime,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
    ) -> LoginThrottle:
        # Two concurrent first failures can both try to insert the key. Retry
        # once so the loser updates the winner's row instead of returning 500.
        for attempt in range(2):
            throttle = await self.session.get(LoginThrottle, key)
            if throttle is None:
                throttle = LoginThrottle(
                    key=key,
                    attempts=0,
                    window_started_at=now,
                )
                self.session.add(throttle)
            else:
                started = _as_utc(throttle.window_started_at)
                if now - started > timedelta(seconds=window_seconds):
                    throttle.attempts = 0
                    throttle.window_started_at = now
                    throttle.locked_until = None

            throttle.attempts += 1
            if throttle.attempts >= max_attempts:
                throttle.locked_until = now + timedelta(seconds=lockout_seconds)
            throttle.updated_at = now
            try:
                await self.session.commit()
            except IntegrityError:
                await self.session.rollback()
                if attempt == 0:
                    continue
                raise
            await self.session.refresh(throttle)
            return throttle
        raise RuntimeError("could not record login failure")

    async def clear_login_throttle(self, key: str) -> None:
        await self.session.execute(
            delete(LoginThrottle).where(LoginThrottle.key == key)
        )
        await self.session.commit()

    async def delete_expired_sessions(self, before: datetime) -> int:
        result = await self.session.execute(
            delete(AuthSession).where(AuthSession.expires_at < before)
        )
        await self.session.commit()
        return int(result.rowcount or 0)


def _user_filters(
    *,
    query: str,
    role: str | None,
    is_active: bool | None,
) -> list:
    """Filters shared by the list and its total count."""
    filters: list = []
    if query:
        pattern = f"%{query.strip().lower()}%"
        filters.append(
            or_(
                func.lower(User.username).like(pattern),
                func.lower(func.coalesce(User.display_name, "")).like(pattern),
            )
        )
    if role is not None:
        filters.append(User.role == role)
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))
    return filters


def _summary_statement(now: datetime):
    """Users plus last-session and active-session aggregates.

    Correlated subqueries keep the page query flat: a join with GROUP BY would
    need every user column listed and would multiply rows if another aggregate
    is added later.
    """
    last_session_at = (
        select(
            func.max(func.coalesce(AuthSession.last_used_at, AuthSession.created_at))
        )
        .where(AuthSession.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )
    active_sessions = (
        select(func.count())
        .select_from(AuthSession)
        .where(AuthSession.user_id == User.id, AuthSession.expires_at > now)
        .correlate(User)
        .scalar_subquery()
    )
    return select(
        User,
        last_session_at.label("last_session_at"),
        active_sessions.label("active_sessions"),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
