"""HTTP API for phone-data sync, mounted under /api/v1/phone.

The phone authenticates with the same bearer session as the other clients.
Every route is scoped to the authenticated user, and a device id in a path is
also checked against that user: knowing another device's uuid is not enough.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from winnie_core.auth.dependencies import get_user_id
from winnie_phone.schemas import (
    AppRead,
    CalendarRead,
    CallRead,
    CommandPropose,
    CommandRead,
    CommandResult,
    ContactRead,
    DeviceRead,
    DeviceRegister,
    LocationRead,
    MessageRead,
    NotificationRead,
    SearchResponse,
    SummaryRead,
    SyncRequest,
    SyncResult,
)
from winnie_phone.service import (
    SEARCH_KINDS,
    CommandNotFound,
    DeviceNotFound,
    DeviceRevoked,
    InvalidCommand,
    PhoneError,
    PhoneService,
)

router = APIRouter(prefix="/phone", tags=["phone"])


def get_service(request: Request) -> PhoneService:
    service = getattr(request.app.state, "phone_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="phone sync is disabled")
    return service


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, (DeviceNotFound, CommandNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DeviceRevoked):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (InvalidCommand, PhoneError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.post("/devices", response_model=DeviceRead, status_code=201)
async def register_device(
    payload: DeviceRegister,
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> DeviceRead:
    try:
        return DeviceRead.model_validate(
            await service.register_device(user_id, payload)
        )
    except PhoneError as exc:
        raise _error(exc) from None


@router.get("/devices", response_model=list[DeviceRead])
async def list_devices(
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> list[DeviceRead]:
    return [
        DeviceRead.model_validate(view)
        for view in await service.list_devices(user_id)
    ]


@router.get("/devices/{device_id}", response_model=DeviceRead)
async def get_device(
    device_id: UUID,
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> DeviceRead:
    try:
        return DeviceRead.model_validate(
            await service.get_device(user_id, device_id)
        )
    except PhoneError as exc:
        raise _error(exc) from None


@router.delete("/devices/{device_id}", status_code=204)
async def revoke_device(
    device_id: UUID,
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> None:
    try:
        await service.revoke_device(user_id, device_id)
    except PhoneError as exc:
        raise _error(exc) from None


@router.post("/devices/{device_id}/restore", response_model=DeviceRead)
async def restore_device(
    device_id: UUID,
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> DeviceRead:
    """Undo a revocation; a revoked phone rejects registration until restored."""
    try:
        return DeviceRead.model_validate(
            await service.restore_device(user_id, device_id)
        )
    except PhoneError as exc:
        raise _error(exc) from None


@router.delete("/devices/{device_id}/data")
async def wipe_device(
    device_id: UUID,
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> dict[str, int]:
    """Delete every synced record for this device; the registration remains."""
    try:
        removed = await service.wipe_device(user_id, device_id)
    except PhoneError as exc:
        raise _error(exc) from None
    return {"removed": removed}


@router.post("/devices/{device_id}/sync", response_model=SyncResult)
async def sync(
    device_id: UUID,
    payload: SyncRequest,
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> SyncResult:
    try:
        return SyncResult.model_validate(
            await service.sync(user_id, device_id, payload)
        )
    except PhoneError as exc:
        raise _error(exc) from None


@router.get("/devices/{device_id}/ids")
async def device_external_ids(
    device_id: UUID,
    kind: str = Query(...),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=5000, ge=1, le=20000),
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> dict[str, object]:
    """Ids the server holds for one kind, for a client-side snapshot diff.

    A full snapshot only fits one request while it is small. A larger one is
    paged as incremental upserts, and the client uses this list to report the
    ids that disappeared.
    """
    try:
        ids = await service.external_ids(
            user_id, device_id, kind, limit=limit, since=since, until=until
        )
    except PhoneError as exc:
        raise _error(exc) from None
    return {"kind": kind, "external_ids": ids}


@router.get("/devices/{device_id}/commands", response_model=list[CommandRead])
async def pending_commands(
    device_id: UUID,
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> list[CommandRead]:
    """Claim commands the owner has already confirmed in chat."""
    try:
        return [
            CommandRead.model_validate(view)
            for view in await service.pending_commands(user_id, device_id)
        ]
    except PhoneError as exc:
        raise _error(exc) from None


@router.post(
    "/devices/{device_id}/commands/{command_id}/result",
    response_model=CommandRead,
)
async def command_result(
    device_id: UUID,
    command_id: UUID,
    payload: CommandResult,
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> CommandRead:
    try:
        view = await service.complete_command(
            user_id,
            command_id,
            status=payload.status,
            result=payload.result,
            error=payload.error,
            device_id=device_id,
        )
    except PhoneError as exc:
        raise _error(exc) from None
    return CommandRead.model_validate(view)


@router.get("/summary", response_model=SummaryRead)
async def summary(
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> SummaryRead:
    return SummaryRead.model_validate(await service.summary(user_id))


@router.get("/calendar", response_model=list[CalendarRead])
async def calendar(
    since: datetime | None = None,
    until: datetime | None = None,
    q: str = "",
    device_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> list[CalendarRead]:
    return [
        CalendarRead.model_validate(row)
        for row in await service.calendar(
            user_id,
            since=since,
            until=until,
            query=q,
            device_id=device_id,
            limit=limit,
        )
    ]


@router.get("/calls", response_model=list[CallRead])
async def calls(
    since: datetime | None = None,
    until: datetime | None = None,
    direction: str = "",
    q: str = "",
    device_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> list[CallRead]:
    return [
        CallRead.model_validate(row)
        for row in await service.calls(
            user_id,
            since=since,
            until=until,
            direction=direction,
            query=q,
            device_id=device_id,
            limit=limit,
        )
    ]


@router.get("/messages", response_model=list[MessageRead])
async def messages(
    since: datetime | None = None,
    until: datetime | None = None,
    address: str = "",
    q: str = "",
    unread_only: bool = False,
    device_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> list[MessageRead]:
    return [
        MessageRead.model_validate(row)
        for row in await service.messages(
            user_id,
            since=since,
            until=until,
            address=address,
            query=q,
            unread_only=unread_only,
            device_id=device_id,
            limit=limit,
        )
    ]


@router.get("/contacts", response_model=list[ContactRead])
async def contacts(
    q: str = "",
    device_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> list[ContactRead]:
    return [
        ContactRead.model_validate(row)
        for row in await service.contacts(
            user_id, query=q, device_id=device_id, limit=limit
        )
    ]


@router.get("/notifications", response_model=list[NotificationRead])
async def notifications(
    since: datetime | None = None,
    until: datetime | None = None,
    app: str = "",
    q: str = "",
    device_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> list[NotificationRead]:
    return [
        NotificationRead.model_validate(row)
        for row in await service.notifications(
            user_id,
            since=since,
            until=until,
            app=app,
            query=q,
            device_id=device_id,
            limit=limit,
        )
    ]


@router.get("/locations", response_model=list[LocationRead])
async def locations(
    since: datetime | None = None,
    until: datetime | None = None,
    device_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> list[LocationRead]:
    return [
        LocationRead.model_validate(row)
        for row in await service.locations(
            user_id, since=since, until=until, device_id=device_id, limit=limit
        )
    ]


@router.get("/apps", response_model=list[AppRead])
async def apps(
    q: str = "",
    device_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> list[AppRead]:
    return [
        AppRead.model_validate(row)
        for row in await service.apps(
            user_id, query=q, device_id=device_id, limit=limit
        )
    ]


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
    kinds: str = "",
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> SearchResponse:
    selected = [kind.strip() for kind in kinds.split(",") if kind.strip()]
    unknown = [kind for kind in selected if kind not in SEARCH_KINDS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown kinds: {', '.join(unknown)}",
        )
    try:
        hits = await service.search(
            user_id, q, kinds=selected or None, since=since, until=until, limit=limit
        )
    except PhoneError as exc:
        raise _error(exc) from None
    names = {
        str(device["id"]): device["name"]
        for device in await service.list_devices(user_id)
    }
    return SearchResponse(query=q, hits=hits, device_names=names)


@router.get("/commands", response_model=list[CommandRead])
async def list_commands(
    status: str = "",
    limit: int = Query(default=20, ge=1, le=100),
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> list[CommandRead]:
    return [
        CommandRead.model_validate(view)
        for view in await service.list_commands(user_id, status=status, limit=limit)
    ]


@router.post("/commands", response_model=CommandRead, status_code=201)
async def propose_command(
    payload: CommandPropose,
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> CommandRead:
    try:
        view = await service.propose_command(
            user_id,
            payload.kind,
            payload.payload,
            device_id=payload.device_id,
        )
    except PhoneError as exc:
        raise _error(exc) from None
    return CommandRead.model_validate(view)


@router.post("/commands/{command_id}/approve", response_model=CommandRead)
async def approve_command(
    command_id: UUID,
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> CommandRead:
    try:
        return CommandRead.model_validate(
            await service.approve_command(user_id, command_id)
        )
    except PhoneError as exc:
        raise _error(exc) from None


@router.post("/commands/{command_id}/cancel", response_model=CommandRead)
async def cancel_command(
    command_id: UUID,
    service: PhoneService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> CommandRead:
    try:
        return CommandRead.model_validate(
            await service.cancel_command(user_id, command_id)
        )
    except PhoneError as exc:
        raise _error(exc) from None
