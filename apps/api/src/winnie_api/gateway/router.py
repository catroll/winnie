"""Console API for Winnie gateway keys.

Keys are Winnie credentials, not vendor credentials: a caller presents one of
these and the gateway injects the supplier key for the route. The secret is
returned exactly once, at creation; afterwards only its id, label, owner,
creation time, allowlist and revocation time are readable.

A key belongs to the account that created it. That account lists, creates and
revokes its own keys without an administrator; an administrator can list every
account's keys and revoke any of them, which is how a departed account's access
is removed. A key with no owner predates ownership and is administrator-only.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from winnie_core.auth.dependencies import Principal, get_principal
from winnie_core.auth.roles import ROLE_ADMIN
from winnie_gateway.keystore import (
    GatewayKey,
    GatewayKeyError,
    GatewayKeyStore,
    UnknownGatewayKey,
)

router = APIRouter(prefix="/gateway", tags=["gateway"])


class KeyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=64)
    routes: list[str] = Field(default_factory=list, max_length=64)


def _store(request: Request) -> GatewayKeyStore:
    store = getattr(request.app.state, "gateway_keys", None)
    if store is None:
        raise HTTPException(status_code=503, detail="gateway key store is not configured")
    return store


def _known_routes(request: Request) -> set[str]:
    table = getattr(request.app.state, "routes_table", {})
    return set(table)


def _check_routes(request: Request, routes: list[str]) -> list[str]:
    """An allowlist may only name routes that exist, or it is a silent 403 later."""
    known = _known_routes(request)
    unknown = [name for name in routes if name not in known]
    if unknown:
        available = ", ".join(sorted(known)) or "none"
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown route(s): {', '.join(unknown)}; "
                f"configured routes: {available}"
            ),
        )
    return routes


def _is_admin(principal: Principal) -> bool:
    return principal.role == ROLE_ADMIN


def _may_manage(key: GatewayKey, principal: Principal) -> bool:
    """Owners manage their keys; administrators manage every key."""
    if key.owner_id == principal.user_id:
        return True
    return _is_admin(principal)


def _visible_to(key: GatewayKey, principal: Principal) -> bool:
    """What a caller's own list contains.

    An administrator also sees ownerless keys, because those were created before
    keys had owners and would otherwise be invisible to the only account that
    can revoke them.
    """
    if key.owner_id == principal.user_id:
        return True
    return _is_admin(principal) and not key.owner_id


@router.get("/keys")
async def list_keys(
    request: Request,
    scope: Literal["own", "all"] = Query(default="own"),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    keys = _store(request).list()
    if scope == "all":
        if not _is_admin(principal):
            raise HTTPException(
                status_code=403, detail="only an administrator may list every key"
            )
    else:
        keys = [key for key in keys if _visible_to(key, principal)]
    return {
        "keys": [key.view() for key in keys],
        # Names only: the console needs them for the allowlist checkboxes, and
        # a route prefix is not secret.
        "routes": sorted(_known_routes(request)),
    }


@router.post("/keys", status_code=201)
async def create_key(
    request: Request,
    payload: KeyDraft,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    routes = _check_routes(request, payload.routes)
    try:
        key, secret = _store(request).create(
            label=payload.label,
            routes=routes,
            owner_id=principal.user_id,
            owner_name=principal.username,
        )
    except GatewayKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"key": key.view(), "secret": secret}


@router.delete("/keys/{key_id}")
async def revoke_key(
    key_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    store = _store(request)
    try:
        key = store.find(key_id)
    except UnknownGatewayKey as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not _may_manage(key, principal):
        # Do not reveal whether another account's key exists.
        raise HTTPException(status_code=404, detail="gateway key not found")
    return {"key": store.revoke(key.id).view()}
