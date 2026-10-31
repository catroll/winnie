"""API models for accounts, sessions and admin user management."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from winnie_core.auth.roles import ROLE_USER, Role


class RegisterRequest(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str | None
    role: Role
    created_at: datetime


class TokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserRead


class AdminUserRead(BaseModel):
    """One account as seen by an operator.

    ``last_session_at`` is the most recent session creation or use, whichever
    is later; ``active_sessions`` counts sessions that have not expired. Neither
    exposes a token or a hash.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str | None
    role: Role
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_session_at: datetime | None = None
    active_sessions: int = 0


class AdminUserListRead(BaseModel):
    items: list[AdminUserRead]
    total: int
    limit: int
    offset: int


class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    display_name: str | None = Field(default=None, max_length=128)
    role: Role = ROLE_USER


class AdminUserUpdate(BaseModel):
    """Partial update; omitted fields are left untouched.

    An explicit ``null`` for ``display_name`` clears it. ``role`` and
    ``is_active`` are ignored when explicitly null because neither has a null
    state.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=128)
    role: Role | None = None
    is_active: bool | None = None


class TemporaryPasswordRead(BaseModel):
    """Returned once by create/reset; neither call stores the plain password."""

    user: AdminUserRead
    temporary_password: str
