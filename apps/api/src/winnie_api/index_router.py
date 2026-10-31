"""Console-facing proxy to the local document index daemon.

The console keeps one origin and one session: it talks to this API, and this API
talks to the index daemon on loopback. Nothing in the browser needs to know the
daemon's port, and the daemon's management endpoints are never exposed to a
remote client.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from winnie_core.auth.dependencies import get_user_id

router = APIRouter(prefix="/index", tags=["index"])


def _client(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "http", None)
    if client is None:
        raise HTTPException(status_code=503, detail="http client is not available")
    return client


def _timeout(request: Request) -> httpx.Timeout:
    configured = getattr(request.app.state, "index_timeout", 60.0)
    return httpx.Timeout(configured, connect=5.0)


def _base(request: Request) -> str:
    base = getattr(request.app.state, "index_base_url", "")
    if not base:
        raise HTTPException(status_code=503, detail="the document index is disabled")
    return base.rstrip("/")


async def _forward(
    request: Request, method: str, path: str, payload: Any = None
) -> dict[str, Any]:
    url = _base(request) + path
    try:
        response = await _client(request).request(
            method, url, json=payload, timeout=_timeout(request)
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"the document index is not reachable at {_base(request)}: {exc}",
        ) from exc

    if response.status_code >= 400:
        detail = "index request failed"
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            detail = str(body.get("error") or body.get("detail") or detail)
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="index returned invalid JSON") from exc


@router.get("/status")
async def index_status(
    request: Request, _: str = Depends(get_user_id)
) -> dict[str, Any]:
    return await _forward(request, "GET", "/admin/status")


@router.get("/config")
async def index_config(
    request: Request, _: str = Depends(get_user_id)
) -> dict[str, Any]:
    return await _forward(request, "GET", "/admin/config")


@router.put("/config")
async def save_index_config(
    request: Request,
    payload: dict[str, Any] = Body(...),
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    return await _forward(request, "PUT", "/admin/config", payload)


@router.post("/search")
async def search_index(
    request: Request,
    payload: dict[str, Any] = Body(...),
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    return await _forward(request, "POST", "/admin/search", payload)


@router.post("/build")
async def build_index(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    return await _forward(request, "POST", "/admin/build", payload)


@router.post("/skill")
async def index_skill(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    return await _forward(request, "POST", "/admin/skill", payload)
