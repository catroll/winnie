"""Console API for Winnie gateway keys.

Keys are Winnie credentials, not vendor credentials: a caller presents one of
these and the gateway injects the supplier key for the route. The secret is
returned exactly once, at creation; afterwards only its id, label, creation
time, allowlist and revocation time are readable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from winnie_core.auth.dependencies import admin_required
from winnie_gateway.keystore import (
    GatewayKeyError,
    GatewayKeyStore,
    UnknownGatewayKey,
)

router = APIRouter(
    prefix="/gateway",
    tags=["gateway"],
    dependencies=[Depends(admin_required)],
)


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


@router.get("/keys")
async def list_keys(request: Request) -> dict[str, Any]:
    return {"keys": [key.view() for key in _store(request).list()]}


@router.post("/keys", status_code=201)
async def create_key(request: Request, payload: KeyDraft) -> dict[str, Any]:
    routes = _check_routes(request, payload.routes)
    try:
        key, secret = _store(request).create(label=payload.label, routes=routes)
    except GatewayKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"key": key.view(), "secret": secret}


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: str, request: Request) -> dict[str, Any]:
    try:
        key = _store(request).revoke(key_id)
    except UnknownGatewayKey as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"key": key.view()}
