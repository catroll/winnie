"""HTTP surface for the model gateway.

Two things live here: the data plane, which forwards every path under
``/{route}/`` unchanged, and the console's route list. Authentication happens
once, here, before any bytes leave the process: the caller presents a Winnie
gateway key, the key is checked against its route allowlist, and the route's
supplier credential is injected by :mod:`winnie_gateway.proxy`.

Every call that reaches this module leaves a record, including the ones this
module itself refuses: a 401 or 403 with no trace on disk is the one gateway
failure an operator cannot reconstruct after the caller reports it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from winnie_core.auth.dependencies import admin_required
from winnie_gateway.keystore import GatewayKey, GatewayKeyStore
from winnie_gateway.proxy import error_response, forward
from winnie_gateway.record import (
    InteractionRecorder,
    client_request_url,
    snapshot_body,
)
from winnie_gateway.routes import Route

logger = logging.getLogger(__name__)

router = APIRouter()

# A route prefix that collides with a host path would shadow it. These names are
# therefore never resolved as routes, even if someone registers a supplier
# endpoint with one of them.
RESERVED_PREFIXES = frozenset(
    {"api", "docs", "redoc", "openapi.json", "healthz", "readyz", "routes"}
)

_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

# A log directory is derived from a URL segment here, so it must never be
# anything a caller chose. Anything that is not a registry-shaped slug is filed
# under one fixed bucket.
_ROUTE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_UNMAPPED_BUCKET = "_unmapped"


@dataclass(frozen=True)
class _Rejection:
    """Why a call was refused before it could be forwarded."""

    status_code: int
    message: str
    error_type: str = "api_error"


def _gateway_keys(request: Request) -> GatewayKeyStore:
    store = getattr(request.app.state, "gateway_keys", None)
    if store is None:
        raise HTTPException(status_code=503, detail="gateway key store is not configured")
    return store


def _get_route(request: Request, route_name: str) -> Route:
    table: dict[str, Route] = request.app.state.routes_table
    route = table.get(route_name)
    if route is None:
        known = ", ".join(sorted(table)) or "none"
        raise HTTPException(
            status_code=404,
            detail=f"unknown route {route_name!r}; known: {known}",
        )
    return route


def _usage(request: Request):
    """The host's usage ledger, when recording is enabled."""
    return getattr(request.app.state, "usage_ledger", None)


def _recorder(request: Request, *, bucket: str, service: str) -> InteractionRecorder:
    cache: dict[str, InteractionRecorder] = request.app.state.recorders
    if bucket not in cache:
        cache[bucket] = InteractionRecorder(
            request.app.state.log_root / bucket,
            service=service,
        )
    return cache[bucket]


def _route_recorder(request: Request, route: Route) -> InteractionRecorder:
    return _recorder(
        request,
        bucket=route.name,
        service=f"{route.protocol}-gateway:{route.name}",
    )


def presented_header(request: Request) -> str:
    """Which header carried the caller's Winnie key, in the order they are read."""
    if request.headers.get("x-winnie-key", "").strip():
        return "x-winnie-key"
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return "authorization"
    if request.headers.get("x-api-key", "").strip():
        return "x-api-key"
    return ""


def presented_key(request: Request) -> str:
    """The Winnie key a client sent, in whichever header its SDK uses."""
    header = presented_header(request)
    if not header:
        return ""
    value = request.headers.get(header, "")
    if header == "authorization":
        return value.partition(" ")[2].strip()
    return value.strip()


def _authenticate(
    request: Request, route: Route
) -> tuple[GatewayKey | None, _Rejection | None]:
    """Resolve the caller's key, or the refusal the client should see."""
    token = presented_key(request)
    if not token:
        return None, _Rejection(
            401,
            "missing gateway API key: send a Winnie key created in the console "
            "(Authorization: Bearer, x-api-key, or x-winnie-key)",
            "authentication_error",
        )
    record = _gateway_keys(request).verify(token)
    if record is None:
        return None, _Rejection(
            401,
            "invalid or revoked gateway API key; create one in the console "
            "(Admin -> Gateway routes)",
            "authentication_error",
        )
    if not record.allows(route.name):
        allowed = ", ".join(record.routes)
        return None, _Rejection(
            403,
            f"gateway key {record.label!r} may not call route {route.name!r}; "
            f"allowed: {allowed}",
            "permission_error",
        )
    return record, None


async def _record_refusal(
    request: Request,
    *,
    recorder: InteractionRecorder,
    status_code: int,
    reason: str,
    route_name: str,
    protocol: str = "",
    key_label: str = "",
) -> None:
    """Write the record a refused call would otherwise not leave behind."""
    body = await request.body()
    written = recorder.rejected(
        client_url=client_request_url(request),
        method=request.method,
        status_code=status_code,
        reason=reason,
        route=route_name,
        protocol=protocol,
        headers=request.headers.items(),
        presented_key=presented_key(request),
        key_label=key_label,
        client_host=request.client.host if request.client else "",
        body=snapshot_body(body),
        body_bytes=len(body),
    )
    if written is None:
        logger.warning(
            "refusal recording is paused for %s: the per-process budget is spent",
            recorder.dir.name,
        )


@router.get("/routes", dependencies=[Depends(admin_required)])
async def list_routes(request: Request) -> dict[str, Any]:
    table: dict[str, Route] = request.app.state.routes_table
    return {
        "routes": [
            route.view() for route in sorted(table.values(), key=lambda r: r.name)
        ]
    }


@router.api_route("/{route}/{path:path}", methods=_PROXY_METHODS)
async def proxy_path(route: str, path: str, request: Request) -> Response:
    if route in RESERVED_PREFIXES:
        await _record_refusal(
            request,
            recorder=_recorder(
                request, bucket=_UNMAPPED_BUCKET, service="gateway:unmapped"
            ),
            status_code=404,
            reason="reserved prefix, not a gateway route",
            route_name=route,
        )
        raise HTTPException(status_code=404, detail="not a gateway route")

    try:
        resolved = _get_route(request, route)
    except HTTPException as exc:
        await _record_refusal(
            request,
            recorder=_recorder(
                request, bucket=_UNMAPPED_BUCKET, service="gateway:unmapped"
            ),
            status_code=exc.status_code,
            reason=str(exc.detail),
            route_name=route,
        )
        raise

    recorder = _route_recorder(request, resolved)
    caller, rejection = _authenticate(request, resolved)
    if rejection is not None:
        await _record_refusal(
            request,
            recorder=recorder,
            status_code=rejection.status_code,
            reason=rejection.message,
            route_name=resolved.name,
            protocol=resolved.protocol,
        )
        return error_response(
            resolved.protocol,
            rejection.status_code,
            rejection.message,
            error_type=rejection.error_type,
        )
    assert caller is not None

    if not resolved.credential_present:
        message = (
            f"route {resolved.name!r} has no server credential; set the API key "
            "of its supplier in Admin -> AI suppliers"
        )
        await _record_refusal(
            request,
            recorder=recorder,
            status_code=503,
            reason=message,
            route_name=resolved.name,
            protocol=resolved.protocol,
            key_label=caller.label,
        )
        return error_response(resolved.protocol, 503, message)

    return await forward(
        request=request,
        route=resolved,
        path=path,
        default_anthropic_version=request.app.state.gateway.anthropic_version,
        client=request.app.state.http,
        recorder=recorder,
        key=caller,
        credential_header=presented_header(request),
        usage=_usage(request),
    )
