"""FastAPI dependencies for bearer authentication and meeting ownership."""

from __future__ import annotations

from fastapi import HTTPException, Request

from winnie_core.auth.models import User


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
