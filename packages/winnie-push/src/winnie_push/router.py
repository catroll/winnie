"""Push delivery: a durable backlog plus a live WebSocket.

The WebSocket is held open by the Android foreground service. REST endpoints
exist for the same client when the socket is down: it can list the backlog after
its last seq and show those events as notifications.

Authentication is the account bearer token, read from the ``Authorization``
header (the Android client sets it on the handshake) or from ``?token=`` for
clients that cannot set headers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket

from winnie_core.auth.dependencies import get_user_id
from winnie_push.schemas import (
    PushEventRead,
    PushList,
    PushStatusRead,
    PushTestRequest,
)
from winnie_push.service import PushService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push"])


def get_service(request: Request) -> PushService:
    service = getattr(request.app.state, "push_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="push is disabled")
    return service


async def _websocket_user_id(websocket: WebSocket) -> str | None:
    app = websocket.scope["app"]
    service = getattr(app.state, "auth_service", None)
    if service is None:
        return str(getattr(app.state, "default_user_id", "local"))

    authorization = websocket.headers.get("authorization", "")
    scheme, _, header_token = authorization.partition(" ")
    token = header_token.strip() if scheme.lower() == "bearer" else ""
    if not token:
        token = websocket.query_params.get("token", "").strip()
    if not token:
        return None
    user = await service.authenticate(token)
    return str(user.id) if user is not None else None


@router.websocket("/ws")
async def stream(
    websocket: WebSocket,
    after: int = Query(default=0, ge=0),
    device_id: UUID | None = Query(default=None),
) -> None:
    """Send the backlog after ``after``, then live events until disconnect."""
    app = websocket.scope["app"]
    service = getattr(app.state, "push_service", None)
    if service is None:
        await websocket.close(code=1013, reason="push is disabled")
        return
    user_id = await _websocket_user_id(websocket)
    if user_id is None:
        await websocket.close(code=4401, reason="invalid or expired session")
        return

    await websocket.accept()
    async with service.hub.subscribe(user_id) as queue:
        try:
            backlog = await service.backlog(
                user_id,
                after_seq=after,
                device_id=device_id,
                limit=service.config.max_backlog,
            )
            for event in backlog:
                await websocket.send_json({"type": "event", **event})
            last_seq = backlog[-1]["seq"] if backlog else after
            await websocket.send_json({"type": "ready", "last_seq": last_seq})
        except Exception:  # noqa: BLE001 - a failed handshake closes the socket
            logger.debug("push backlog failed", exc_info=True)
            with suppress(Exception):
                await websocket.close(code=1011, reason="backlog failed")
            return

        send_task = asyncio.create_task(
            _send_loop(websocket, service, queue, device_id)
        )
        receive_task = asyncio.create_task(_receive_loop(websocket))
        try:
            await asyncio.wait(
                {send_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (send_task, receive_task):
                task.cancel()
            for task in (send_task, receive_task):
                with suppress(asyncio.CancelledError, Exception):
                    await task


async def _send_loop(
    websocket: WebSocket,
    service: PushService,
    queue: asyncio.Queue,
    device_id: UUID | None,
) -> None:
    while True:
        try:
            event = await asyncio.wait_for(
                queue.get(), timeout=service.config.heartbeat_seconds
            )
        except TimeoutError:
            # Keeps the connection (and any NAT mapping) alive, and tells the
            # client the server is still there.
            await websocket.send_json({"type": "ping"})
            continue
        target = event.get("device_id")
        if target is not None and str(target) != str(device_id):
            continue
        await websocket.send_json({"type": "event", **event})


async def _receive_loop(websocket: WebSocket) -> None:
    while True:
        try:
            raw = await websocket.receive_text()
        except Exception:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "ping":
            await websocket.send_json({"type": "pong"})


@router.get("/notifications", response_model=PushList)
async def notifications(
    after_seq: int = Query(default=0, ge=0),
    device_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    service: PushService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> PushList:
    events = await service.backlog(
        user_id, after_seq=after_seq, device_id=device_id, limit=limit
    )
    last_seq = events[-1]["seq"] if events else after_seq
    return PushList(
        events=[PushEventRead.model_validate(event) for event in events],
        last_seq=last_seq,
    )


@router.get("/status", response_model=PushStatusRead)
async def status(
    service: PushService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> PushStatusRead:
    return PushStatusRead.model_validate(await service.status(user_id))


@router.post("/test", response_model=PushEventRead, status_code=201)
async def test_notification(
    payload: PushTestRequest,
    service: PushService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> PushEventRead:
    """Publish a notification to the caller's devices.

    This is the end-to-end check for the app's background connection: if the
    foreground service is connected, the notification appears immediately.
    """
    event = await service.publish(
        user_id,
        title=payload.title,
        body=payload.body,
        kind="notification",
        data={"test": True},
    )
    return PushEventRead.model_validate(event)
