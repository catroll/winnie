"""HTTP routes for the transparent OpenAI/Anthropic gateway."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from winnie_gateway.anthropic import proxy_anthropic_models, proxy_messages
from winnie_gateway.openai import proxy_chat_completions, proxy_models
from winnie_gateway.record import InteractionRecorder
from winnie_gateway.routes import Route
from winnie_gateway.config import GatewayConfig

router = APIRouter()


def require_client_auth(request: Request) -> None:
    """Optionally check the shared proxy key; vendor keys stay client-side."""
    settings: GatewayConfig = request.app.state.gateway
    expected = settings.proxy_api_key
    if not expected:
        return

    authorization = request.headers.get("authorization")
    x_api_key = request.headers.get("x-api-key")
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    elif x_api_key:
        token = x_api_key.strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing proxy API key")


def _get_route(request: Request, route_name: str) -> Route:
    table: dict[str, Route] = request.app.state.routes_table
    route = table.get(route_name)
    if route is None:
        known = ", ".join(sorted(table))
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
            service=f"{route.protocol}-proxy:{route.name}",
        )
    return cache[route.name]


def _require_protocol(route: Route, expected: str) -> None:
    if route.protocol != expected:
        raise HTTPException(
            status_code=404,
            detail=(
                f"route {route.name!r} is protocol={route.protocol!r}, "
                f"not {expected!r}"
            ),
        )


def _anthropic_bad_request(message: str) -> dict[str, Any]:
    return {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": message},
    }


@router.get("/routes")
async def list_routes(request: Request) -> dict[str, Any]:
    table: dict[str, Route] = request.app.state.routes_table
    return {
        "routes": [
            {
                "name": r.name,
                "protocol": r.protocol,
                "base_url": r.base_url,
                "api_base": r.api_base(),
                "paths": (
                    [f"/{r.name}/v1/chat/completions", f"/{r.name}/v1/models"]
                    if r.protocol == "openai"
                    else [f"/{r.name}/v1/messages", f"/{r.name}/v1/models"]
                ),
            }
            for r in sorted(table.values(), key=lambda x: x.name)
        ]
    }


@router.get("/{route}/v1/models", dependencies=[Depends(require_client_auth)])
async def list_models(route: str, request: Request):
    r = _get_route(request, route)
    if r.protocol == "anthropic":
        return await proxy_anthropic_models(
            client=request.app.state.http,
            route=r,
            request=request,
            default_version=request.app.state.gateway.anthropic_version,
        )
    return await proxy_models(
        client=request.app.state.http,
        route=r,
        request=request,
    )


@router.post("/{route}/v1/chat/completions", dependencies=[Depends(require_client_auth)])
async def chat_completions(route: str, request: Request):
    r = _get_route(request, route)
    _require_protocol(r, "openai")
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": f"invalid json: {exc}",
                    "type": "invalid_request_error",
                }
            },
        )
    if "messages" not in body:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "messages is required",
                    "type": "invalid_request_error",
                }
            },
        )
    return await proxy_chat_completions(
        client=request.app.state.http,
        route=r,
        recorder=_recorder(request, r),
        request=request,
        body=body,
        usage=_usage(request),
    )


@router.post("/{route}/v1/messages", dependencies=[Depends(require_client_auth)])
async def anthropic_messages(route: str, request: Request):
    r = _get_route(request, route)
    _require_protocol(r, "anthropic")
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=400,
            content=_anthropic_bad_request(f"invalid json: {exc}"),
        )
    missing = [k for k in ("model", "messages", "max_tokens") if k not in body]
    if missing:
        return JSONResponse(
            status_code=400,
            content=_anthropic_bad_request(
                f"missing required fields: {', '.join(missing)}"
            ),
        )
    return await proxy_messages(
        client=request.app.state.http,
        route=r,
        recorder=_recorder(request, r),
        request=request,
        body=body,
        default_version=request.app.state.gateway.anthropic_version,
        usage=_usage(request),
    )
