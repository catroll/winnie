"""Operator-only account management.

The service owns the invariants an administrator cannot bypass: the last active
administrator cannot be demoted or deactivated, an administrator cannot
deactivate their own account, and password reset or deactivation revokes every
session belonging to the target. The router maps the domain errors to HTTP.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_core.auth.dependencies import Principal
from winnie_core.auth.errors import (
    LastActiveAdmin,
    SelfDeactivation,
    UserNotFound,
)
from winnie_core.auth.models import utcnow
from winnie_core.auth.repository import AuthRepository
from winnie_core.auth.roles import ROLE_ADMIN, ROLES
from winnie_core.auth.service import (
    generate_temporary_password,
    validate_username,
)

_UNSET = object()


@dataclass(frozen=True)
class AdminUserView:
    """A user row plus the cheap session summary shown in the console."""

    id: uuid.UUID
    username: str
    display_name: str | None
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_session_at: datetime | None
    active_sessions: int


class AdminUserService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        hasher: PasswordHasher | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.hasher = hasher or PasswordHasher()

    async def list_users(
        self,
        *,
        query: str = "",
        role: str | None = None,
        is_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AdminUserView], int]:
        async with self.session_factory() as session:
            repository = AuthRepository(session)
            rows, total = await repository.list_user_summaries(
                query=query,
                role=role,
                is_active=is_active,
                now=utcnow(),
                limit=limit,
                offset=offset,
            )
            return [_view(*row) for row in rows], total

    async def create_user(
        self,
        *,
        username: str,
        display_name: str | None,
        role: str,
    ) -> tuple[AdminUserView, str]:
        normalized = validate_username(username)
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        password = generate_temporary_password()
        password_hash = await asyncio.to_thread(self.hasher.hash, password)
        async with self.session_factory() as session:
            repository = AuthRepository(session)
            user = await repository.create_user(
                username=normalized,
                password_hash=password_hash,
                display_name=display_name,
                role=role,
            )
            row = await repository.get_user_summary(user.id, now=utcnow())
        assert row is not None
        return _view(*row), password

    async def reset_password(self, user_id: uuid.UUID) -> tuple[AdminUserView, str]:
        async with self.session_factory() as session:
            repository = AuthRepository(session)
            user = await repository.get_user(user_id)
            if user is None:
                raise UserNotFound(f"no account with id {user_id}")
            password = generate_temporary_password()
            password_hash = await asyncio.to_thread(self.hasher.hash, password)
            await repository.update_password_hash(user.id, password_hash)
            # A reset is an ownership handover: every existing bearer token is
            # no longer proof that the original person is using the account.
            await repository.delete_sessions_for_user(user.id)
            row = await repository.get_user_summary(user.id, now=utcnow())
        assert row is not None
        return _view(*row), password

    async def update_user(
        self,
        user_id: uuid.UUID,
        *,
        actor: Principal,
        display_name: str | None | object = _UNSET,
        role: str | object = _UNSET,
        is_active: bool | object = _UNSET,
    ) -> AdminUserView:
        new_role = None if role is _UNSET else str(role)
        if new_role is not None and new_role not in ROLES:
            raise ValueError(f"unknown role {new_role!r}")
        new_active = None if is_active is _UNSET else bool(is_active)

        async with self.session_factory() as session:
            repository = AuthRepository(session)
            target = await repository.get_user(user_id)
            if target is None:
                raise UserNotFound(f"no account with id {user_id}")

            if new_active is False and actor.user_id == str(target.id):
                raise SelfDeactivation(
                    "you cannot deactivate your own account"
                )

            if target.role == ROLE_ADMIN and target.is_active:
                demoting = new_role is not None and new_role != ROLE_ADMIN
                deactivating = new_active is False
                if demoting or deactivating:
                    await repository.lock_active_admins()
                    remaining = await repository.count_active_admins(
                        exclude_user_id=target.id
                    )
                    if remaining == 0:
                        raise LastActiveAdmin(
                            "at least one active administrator must remain"
                        )

            if display_name is not _UNSET:
                target.display_name = cast("str | None", display_name)
            if new_role is not None:
                target.role = new_role
            if new_active is not None:
                target.is_active = new_active
            await repository.save_user(target)

            if new_active is False:
                # Deactivation must also end live sessions; otherwise a browser
                # keeps a token that only fails on its next request.
                await repository.delete_sessions_for_user(target.id)

            row = await repository.get_user_summary(target.id, now=utcnow())
        assert row is not None
        return _view(*row)


def _view(
    user,
    last_session_at: datetime | None,
    active_sessions: int,
) -> AdminUserView:
    return AdminUserView(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_session_at=last_session_at,
        active_sessions=active_sessions,
    )
