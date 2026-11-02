"""HTTP surface for the model gateway.

Two things live here: the data plane, which forwards every path under
``/{route}/`` unchanged, and the console's route list. Authentication happens
once, here, before any bytes leave the process: the caller presents a Winnie
gateway key, the key is checked against its route allowlist, and the route's
vendor credential is injected by :mod:`winnie_gateway.proxy`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from winnie_core.auth.dependencies import admin_required
from winnie_gateway.keystore import GatewayKey, GatewayKeyStore
from winnie_gateway.proxy import error_response, forward
from winnie_gateway.record import InteractionRecorder
from winnie_gateway.routes import Route

router = APIRouter()

# A route prefix that collides with a host path would shadow it. These names are
# therefore never resolved as routes, even if someone registers a supplier
# endpoint with one of them.
RESERVED_PREFIXES = frozenset(
    {"api", "docs", "redoc", "openapi.json", "healthz", "readyz", "routes"}
)

_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


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


def _recorder(request: Request, route: Route) -> InteractionRecorder:
    cache: dict[str, InteractionRecorder] = request.app.state.recorders
    if route.name not in cache:
        cache[route.name] = InteractionRecorder(
            request.app.state.log_root / route.name,
            service=f"{route.protocol}-gateway:{route.name}",
        )
    return cache[route.name]


def presented_key(request: Request) -> str:
    """The Winnie key a client sent, in whichever header its SDK uses."""
    explicit = request.headers.get("x-winnie-key", "").strip()
    if explicit:
        return explicit
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return request.headers.get("x-api-key", "").strip()


def _authenticate(request: Request, route: Route) -> GatewayKey | JSONResponse:
    """Resolve the caller's key or return the error the client should see."""
    store = _gateway_keys(request)
    token = presented_key(request)
    if not token:
        return error_response(
            route.protocol,
            401,
            "missing gateway API key: send a Winnie key created in the console "
            "(Authorization: Bearer, x-api-key, or x-winnie-key)",
            error_type="authentication_error",
        )
    record = store.verify(token)
    if record is None:
        return error_response(
            route.protocol,
            401,
            "invalid or revoked gateway API key; create one in the console "
            "(Admin -> Gateway routes)",
            error_type="authentication_error",
        )
    if not record.allows(route.name):
        allowed = ", ".join(record.routes)
        return error_response(
            route.protocol,
            403,
            f"gateway key {record.label!r} may not call route {route.name!r}; "
            f"allowed: {allowed}",
            error_type="permission_error",
        )
    return record


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
        raise HTTPException(status_code=404, detail="not a gateway route")
    resolved = _get_route(request, route)
    caller = _authenticate(request, resolved)
    if isinstance(caller, JSONResponse):
        return caller
    if not resolved.credential_present:
        return error_response(
            resolved.protocol,
            503,
            f"route {resolved.name!r} has no server credential; set the API key "
            "of its supplier in Admin -> AI suppliers",
        )
    return await forward(
        request=request,
        route=resolved,
        path=path,
        default_anthropic_version=request.app.state.gateway.anthropic_version,
        client=request.app.state.http,
        recorder=_recorder(request, resolved),
        key=caller,
        usage=_usage(request),
    )
