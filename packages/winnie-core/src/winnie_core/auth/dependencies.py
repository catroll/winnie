"""FastAPI dependencies for bearer authentication and authorization."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from winnie_core.auth.models import User
from winnie_core.auth.roles import ROLE_ADMIN


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of a request, with the role read from the store.

    ``is_local`` marks the synthetic operator used when accounts are disabled.
    The role is never cached across requests: a demotion takes effect on the
    caller's next request because ``AuthService.authenticate`` reloads the user.
    """

    user_id: str
    username: str
    role: str
    is_local: bool = False


def bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


async def get_current_user(request: Request) -> User:
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="authentication is disabled")
    user = await service.authenticate(bearer_token(request))
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_principal(request: Request) -> Principal:
    """Resolve the caller, or the local operator when accounts are disabled.

    An auth-disabled deployment is a single trusted operator on the host: the
    configured local user acts as an administrator so the console keeps working
    without a session. Enabling accounts switches this to the session's role.
    """
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        local_id = str(
            getattr(request.app.state, "default_user_id", "local") or "local"
        )
        return Principal(
            user_id=local_id,
            username=local_id,
            role=ROLE_ADMIN,
            is_local=True,
        )
    user = await get_current_user(request)
    return Principal(user_id=str(user.id), username=user.username, role=user.role)


async def admin_required(principal: Principal = Depends(get_principal)) -> Principal:
    """Reject callers that are not administrators.

    Used as a router-level dependency so an operator router cannot accidentally
    expose one route without the check. It must run before route-specific
    dependencies, otherwise a disabled feature would answer 503 instead of 403.
    """
    if principal.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="administrator role required",
        )
    return principal


async def get_user_id(request: Request) -> str:
    """Resolve the owner of a meeting.

    With accounts enabled this is the authenticated user's id. With auth
    disabled the API falls back to the configured local user so the file-first
    demo still works without a database-backed login.
    """
    if getattr(request.app.state, "auth_service", None) is None:
        return getattr(request.app.state, "default_user_id", "local")
    user = await get_current_user(request)
    return str(user.id)
