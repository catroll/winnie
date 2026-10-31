"""Account registration, login and bearer-session authentication."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_core.auth.errors import (
    InvalidCredentials,
    LoginLocked,
    RegistrationClosed,
    UserNotFound,
    UsernameTaken,
)
from winnie_core.auth.models import User, utcnow
from winnie_core.auth.repository import AuthRepository
from winnie_core.auth.roles import ROLE_ADMIN, ROLE_USER
from winnie_core.config import AuthConfig

MIN_PASSWORD_LENGTH = 8
USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{3,64}$")


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> str:
    """Normalize and validate an account name for both register and admin create."""
    normalized = normalize_username(username)
    if USERNAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            "username must be 3-64 characters of letters, digits, '.', '_' or '-'"
        )
    return normalized


def generate_temporary_password() -> str:
    """A one-time password for admin create/reset; shown once, never stored."""
    return secrets.token_urlsafe(12)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        config: AuthConfig,
    ) -> None:
        self.session_factory = session_factory
        self.config = config
        self.hasher = PasswordHasher()
        # Serializes the first-user check within this process. Multi-process
        # deployments should enforce the same rule in the database.
        self._registration_lock = asyncio.Lock()

    async def register(
        self,
        *,
        username: str,
        password: str,
        display_name: str | None = None,
    ) -> tuple[User, str, datetime]:
        normalized = validate_username(username)
        _validate_password(password)

        async with self._registration_lock:
            async with self.session_factory() as session:
                repository = AuthRepository(session)
                existing = await repository.count_users()
                if not self.config.allow_registration and existing > 0:
                    raise RegistrationClosed(
                        "registration is closed; an account already exists"
                    )
                if await repository.get_user_by_username(normalized) is not None:
                    raise UsernameTaken(normalized)
                password_hash = await asyncio.to_thread(self.hasher.hash, password)
                user = await repository.create_user(
                    username=normalized,
                    password_hash=password_hash,
                    display_name=display_name,
                    # Bootstrap: whoever registers on an empty table owns the
                    # operator surface. Later registrations are ordinary users;
                    # an administrator can promote them explicitly.
                    role=ROLE_ADMIN if existing == 0 else ROLE_USER,
                )

            token, expires_at = await self._issue_session(user.id)
        return user, token, expires_at

    async def login(
        self,
        *,
        username: str,
        password: str,
    ) -> tuple[User, str, datetime]:
        normalized = normalize_username(username)
        await self._raise_if_login_locked(normalized)

        try:
            async with self.session_factory() as session:
                repository = AuthRepository(session)
                user = await repository.get_user_by_username(normalized)
                if user is None or not user.is_active:
                    raise InvalidCredentials("invalid username or password")
                if not await self._verify_password(user.password_hash, password):
                    raise InvalidCredentials("invalid username or password")
                if self.hasher.check_needs_rehash(user.password_hash):
                    new_hash = await asyncio.to_thread(self.hasher.hash, password)
                    await repository.update_password_hash(user.id, new_hash)
        except InvalidCredentials:
            await self._record_login_failure(normalized)
            raise

        await self._clear_login_throttle(normalized)
        token, expires_at = await self._issue_session(user.id)
        return user, token, expires_at

    async def authenticate(self, token: str) -> User | None:
        token_hash = hash_token(token)
        async with self.session_factory() as session:
            repository = AuthRepository(session)
            row = await repository.get_session_with_user(token_hash)
            if row is None:
                return None
            auth_session, user = row
            if not user.is_active:
                return None
            expires_at = auth_session.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= utcnow():
                await repository.delete_session(auth_session.id)
                return None
            await repository.touch_session(auth_session.id, utcnow())
            return user

    async def reset_password(
        self,
        *,
        username: str,
        new_password: str,
    ) -> None:
        """Operator-only reset: no current password, all sessions revoked."""
        _validate_password(new_password)
        normalized = normalize_username(username)
        async with self.session_factory() as session:
            repository = AuthRepository(session)
            user = await repository.get_user_by_username(normalized)
            if user is None:
                raise UserNotFound(f"no account named {normalized!r}")
            new_hash = await asyncio.to_thread(self.hasher.hash, new_password)
            await repository.update_password_hash(user.id, new_hash)
            await repository.delete_sessions_for_user(user.id)

    async def logout(self, token: str) -> None:
        async with self.session_factory() as session:
            await AuthRepository(session).delete_session_by_hash(hash_token(token))

    async def change_password(
        self,
        *,
        user_id: uuid.UUID,
        current_password: str,
        new_password: str,
    ) -> None:
        """Change the password and invalidate every session, including this one."""
        _validate_password(new_password)
        if current_password == new_password:
            raise ValueError("new password must differ from the current password")

        async with self.session_factory() as session:
            repository = AuthRepository(session)
            user = await repository.get_user(user_id)
            if (
                user is None
                or not user.is_active
                or not await self._verify_password(
                    user.password_hash, current_password
                )
            ):
                raise InvalidCredentials("current password is incorrect")
            new_hash = await asyncio.to_thread(self.hasher.hash, new_password)
            await repository.update_password_hash(user.id, new_hash)
            await repository.delete_sessions_for_user(user.id)

    async def cleanup_expired_sessions(self, *, before: datetime | None = None) -> int:
        """Delete sessions that can no longer authenticate.

        Expired sessions are already rejected by ``authenticate``; this only
        reclaims rows so the table does not grow without bound.
        """
        cutoff = before or utcnow()
        async with self.session_factory() as session:
            return await AuthRepository(session).delete_expired_sessions(cutoff)

    async def _raise_if_login_locked(self, key: str) -> None:
        now = utcnow()
        async with self.session_factory() as session:
            repository = AuthRepository(session)
            throttle = await repository.get_login_throttle(key)
            if throttle is None or throttle.locked_until is None:
                return
            locked_until = throttle.locked_until
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc)
            if locked_until > now:
                retry_after = max(1, int((locked_until - now).total_seconds()) + 1)
                raise LoginLocked(retry_after)
            await repository.clear_login_throttle(key)

    async def _record_login_failure(self, key: str) -> None:
        config = self.config
        async with self.session_factory() as session:
            await AuthRepository(session).record_login_failure(
                key,
                now=utcnow(),
                max_attempts=config.login_max_attempts,
                window_seconds=config.login_window_seconds,
                lockout_seconds=config.login_lockout_seconds,
            )

    async def _clear_login_throttle(self, key: str) -> None:
        async with self.session_factory() as session:
            await AuthRepository(session).clear_login_throttle(key)

    async def _issue_session(self, user_id: uuid.UUID) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)
        expires_at = utcnow() + timedelta(days=self.config.session_ttl_days)
        async with self.session_factory() as session:
            await AuthRepository(session).create_session(
                user_id=user_id,
                token_hash=hash_token(token),
                expires_at=expires_at,
            )
        return token, expires_at

    async def _verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return await asyncio.to_thread(
                self.hasher.verify, password_hash, password
            )
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


def _validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
