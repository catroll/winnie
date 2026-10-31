"""Operator-only HTTP API for user management.

Mounted at ``/api/v1/admin/users``. Every route depends on ``admin_required``,
so a normal user gets 403 even before the route looks at its input. The service
enforces the last-administrator and self-deactivation invariants; these routes
only translate input and domain errors.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from winnie_core.auth.admin_service import AdminUserService, AdminUserView
from winnie_core.auth.dependencies import Principal, admin_required
from winnie_core.auth.errors import (
    AuthError,
    LastActiveAdmin,
    SelfDeactivation,
    UserNotFound,
    UsernameTaken,
)
from winnie_core.auth.roles import Role
from winnie_core.auth.schemas import (
    AdminUserCreate,
    AdminUserListRead,
    AdminUserRead,
    AdminUserUpdate,
    TemporaryPasswordRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/users", tags=["admin"])


def get_service(request: Request) -> AdminUserService:
    service = getattr(request.app.state, "admin_user_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="user management requires a database-backed runtime",
        )
    return service


def _error(exc: AuthError) -> HTTPException:
    if isinstance(exc, UserNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (LastActiveAdmin, UsernameTaken)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, SelfDeactivation):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _read(view: AdminUserView) -> AdminUserRead:
    return AdminUserRead.model_validate(view)


def _audit(action: str, actor: Principal, target: str, result: str = "ok") -> None:
    """One line per change; never include a password, token or hash."""
    logger.info(
        "admin user action=%s actor=%s target=%s result=%s",
        action,
        actor.username,
        target,
        result,
    )


@router.get("", response_model=AdminUserListRead)
async def list_users(
    q: str = Query(default="", max_length=128),
    role: Role | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: Principal = Depends(admin_required),
    service: AdminUserService = Depends(get_service),
) -> AdminUserListRead:
    """Users ordered oldest first, with a total for pagination.

    ``q`` matches username or display name, case-insensitively. ``role`` and
    ``is_active`` are optional filters; every filter also applies to ``total``.
    """
    users, total = await service.list_users(
        query=q,
        role=role,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return AdminUserListRead(
        items=[_read(view) for view in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=TemporaryPasswordRead, status_code=201)
async def create_user(
    payload: AdminUserCreate,
    actor: Principal = Depends(admin_required),
    service: AdminUserService = Depends(get_service),
) -> TemporaryPasswordRead:
    """Create an account and return its generated password once."""
    try:
        view, password = await service.create_user(
            username=payload.username,
            display_name=payload.display_name,
            role=payload.role,
        )
    except (AuthError, ValueError) as exc:
        _audit("create", actor, payload.username, "refused")
        if isinstance(exc, AuthError):
            raise _error(exc) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit("create", actor, view.username)
    return TemporaryPasswordRead(user=_read(view), temporary_password=password)


@router.patch("/{user_id}", response_model=AdminUserRead)
async def update_user(
    user_id: uuid.UUID,
    payload: AdminUserUpdate,
    actor: Principal = Depends(admin_required),
    service: AdminUserService = Depends(get_service),
) -> AdminUserRead:
    """Change display name, role or active state; omitted fields are untouched."""
    changes: dict[str, Any] = {}
    if "display_name" in payload.model_fields_set:
        changes["display_name"] = payload.display_name
    if "role" in payload.model_fields_set and payload.role is not None:
        changes["role"] = payload.role
    if "is_active" in payload.model_fields_set and payload.is_active is not None:
        changes["is_active"] = payload.is_active
    try:
        view = await service.update_user(user_id, actor=actor, **changes)
    except (AuthError, ValueError) as exc:
        _audit(_action_for(changes), actor, str(user_id), "refused")
        if isinstance(exc, AuthError):
            raise _error(exc) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(_action_for(changes), actor, view.username)
    return _read(view)


@router.post("/{user_id}/reset-password", response_model=TemporaryPasswordRead)
async def reset_password(
    user_id: uuid.UUID,
    actor: Principal = Depends(admin_required),
    service: AdminUserService = Depends(get_service),
) -> TemporaryPasswordRead:
    """Generate a new password and revoke every session the target had."""
    try:
        view, password = await service.reset_password(user_id)
    except AuthError as exc:
        _audit("reset-password", actor, str(user_id), "refused")
        raise _error(exc) from exc
    _audit("reset-password", actor, view.username)
    return TemporaryPasswordRead(user=_read(view), temporary_password=password)


@router.post("/{user_id}/activate", response_model=AdminUserRead)
async def activate_user(
    user_id: uuid.UUID,
    actor: Principal = Depends(admin_required),
    service: AdminUserService = Depends(get_service),
) -> AdminUserRead:
    try:
        view = await service.update_user(user_id, actor=actor, is_active=True)
    except AuthError as exc:
        _audit("activate", actor, str(user_id), "refused")
        raise _error(exc) from exc
    _audit("activate", actor, view.username)
    return _read(view)


@router.post("/{user_id}/deactivate", response_model=AdminUserRead)
async def deactivate_user(
    user_id: uuid.UUID,
    actor: Principal = Depends(admin_required),
    service: AdminUserService = Depends(get_service),
) -> AdminUserRead:
    try:
        view = await service.update_user(user_id, actor=actor, is_active=False)
    except AuthError as exc:
        _audit("deactivate", actor, str(user_id), "refused")
        raise _error(exc) from exc
    _audit("deactivate", actor, view.username)
    return _read(view)


def _action_for(changes: dict[str, Any]) -> str:
    if changes.get("is_active") is False:
        return "deactivate"
    if changes.get("is_active") is True:
        return "activate"
    if "role" in changes:
        return "change-role"
    return "update"
