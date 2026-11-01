"""The console's Inbox and the acknowledgement link.

Acknowledgement is the point of the endpoints: a notification that cannot be
confirmed is only a delivery attempt. The email link carries the item's own
token, so it works without a session; every other endpoint authenticates the
account like the rest of the API.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from winnie_core.auth.dependencies import get_user_id
from winnie_proactive.service import ProactiveError, ProactiveService

router = APIRouter(prefix="/proactive", tags=["proactive"])


def get_service(request: Request) -> ProactiveService:
    service = getattr(request.app.state, "proactive_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="proactive assistance is disabled")
    return service


@router.get("/items")
async def items(
    request: Request,
    status: str = Query(default=""),
    kind: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    user_id: str = Depends(get_user_id),
) -> list[dict]:
    service = get_service(request)
    return await service.items(user_id, status=status, kind=kind, limit=limit)


@router.post("/items/{item_id}/ack")
async def ack(
    request: Request,
    item_id: str,
    payload: dict | None = None,
    user_id: str = Depends(get_user_id),
) -> dict:
    service = get_service(request)
    via = str((payload or {}).get("via") or "console")
    try:
        return await service.ack(user_id, item_id, via=via)
    except ProactiveError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/items/{item_id}/snooze")
async def snooze(
    request: Request,
    item_id: str,
    payload: dict | None = None,
    user_id: str = Depends(get_user_id),
) -> dict:
    service = get_service(request)
    minutes = int((payload or {}).get("minutes") or 60)
    try:
        return await service.snooze(user_id, item_id, minutes=minutes)
    except ProactiveError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/items/{item_id}/mute")
async def mute(
    request: Request,
    item_id: str,
    user_id: str = Depends(get_user_id),
) -> dict:
    service = get_service(request)
    try:
        return await service.mute(user_id, item_id)
    except ProactiveError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/scan")
async def scan(
    request: Request, user_id: str = Depends(get_user_id)
) -> dict:
    """Run the planner now. A diagnostic and a test hook, not a schedule."""
    service = get_service(request)
    return {"created": await service.scan()}


@router.post("/test")
async def test(
    request: Request,
    payload: dict | None = None,
    user_id: str = Depends(get_user_id),
) -> dict:
    """Send one real item now, so the owner can try the notification actions.

    An optional ``channel`` targets one rung; without it the ladder picks the
    first available channel.
    """
    service = get_service(request)
    channel = str((payload or {}).get("channel") or "")
    try:
        return await service.test_message(channel=channel)
    except ProactiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/status")
async def status(
    request: Request, user_id: str = Depends(get_user_id)
) -> dict:
    return await get_service(request).status()


@router.post("/subjects/{name}/prefer")
async def prefer(
    request: Request,
    name: str,
    payload: dict | None = None,
    user_id: str = Depends(get_user_id),
) -> dict:
    """Record which source owns a person's birthday, so the conflict stops."""
    service = get_service(request)
    value = str((payload or {}).get("prefer") or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="prefer is required")
    try:
        service.set_prefer(name, value)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"subject": f"person:{name}", "prefer": value}


@router.get("/ack", response_class=HTMLResponse)
async def ack_link(request: Request, token: str = Query(default="")) -> HTMLResponse:
    """The email/ntfy acknowledgement link. The token is the credential."""
    service = get_service(request)
    if not token:
        return HTMLResponse("<h1>链接无效</h1>", status_code=422)
    item = await service.ack_by_token(token)
    if item is None:
        return HTMLResponse("<h1>链接无效或已过期</h1>", status_code=404)
    return HTMLResponse(
        "<h1>已收到</h1><p>"
        + html.escape(str(item.get("title") or ""))
        + "</p><p>可以关掉这个页面了。</p>"
    )
