"""HTTP API for the meetings context, mounted under /api/v1."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

from winnie_core.auth.dependencies import get_user_id
from winnie_meetings.schemas import (
    MeetingCreate,
    MeetingList,
    MeetingRead,
    MinutesRead,
    SegmentRead,
    TranscriptRead,
)
from winnie_meetings.service import (
    MeetingNotFound,
    MeetingNotReady,
    MeetingQueueError,
    MeetingService,
)

router = APIRouter(prefix="/meetings", tags=["meetings"])


def get_service(request: Request) -> MeetingService:
    service = getattr(request.app.state, "meeting_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="meeting notes are disabled")
    return service


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="meeting not found")


@router.post("", response_model=MeetingRead, status_code=201)
async def create_meeting(
    payload: MeetingCreate,
    service: MeetingService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> MeetingRead:
    meeting = await service.create_meeting(
        user_id=user_id,
        title=payload.title,
        local_language=payload.local_language,
    )
    return MeetingRead.model_validate(meeting)


@router.get("", response_model=MeetingList)
async def list_meetings(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: MeetingService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> MeetingList:
    meetings, total = await service.list_meetings(
        user_id=user_id, limit=limit, offset=offset
    )
    return MeetingList(
        items=[MeetingRead.model_validate(meeting) for meeting in meetings],
        total=total,
    )


@router.get("/{meeting_id}", response_model=MeetingRead)
async def get_meeting(
    meeting_id: UUID,
    service: MeetingService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> MeetingRead:
    try:
        meeting = await service.get_meeting(meeting_id, user_id=user_id)
    except MeetingNotFound:
        raise _not_found() from None
    return MeetingRead.model_validate(meeting)


@router.post("/{meeting_id}/audio", response_model=MeetingRead)
async def upload_audio(
    meeting_id: UUID,
    file: UploadFile = File(...),
    service: MeetingService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> MeetingRead:
    async def chunks():
        while chunk := await file.read(1024 * 1024):
            yield chunk

    max_bytes = service.config.max_upload_mb * 1024 * 1024
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"upload exceeds the {max_bytes} byte limit",
        )

    try:
        meeting = await service.upload_audio(
            meeting_id,
            user_id=user_id,
            filename=file.filename or "audio.bin",
            chunks=chunks(),
        )
    except MeetingNotFound:
        raise _not_found() from None
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return MeetingRead.model_validate(meeting)


@router.post("/{meeting_id}/transcribe", response_model=MeetingRead, status_code=202)
async def transcribe_meeting(
    meeting_id: UUID,
    service: MeetingService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> MeetingRead:
    try:
        meeting = await service.request_processing(meeting_id, user_id=user_id)
    except MeetingNotFound:
        raise _not_found() from None
    except MeetingNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MeetingQueueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return MeetingRead.model_validate(meeting)


@router.get("/{meeting_id}/transcript", response_model=TranscriptRead)
async def get_transcript(
    meeting_id: UUID,
    service: MeetingService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> TranscriptRead:
    try:
        meeting, segments = await service.get_transcript(
            meeting_id, user_id=user_id
        )
    except MeetingNotFound:
        raise _not_found() from None
    return TranscriptRead(
        meeting_id=meeting.id,
        segments=[SegmentRead.model_validate(segment) for segment in segments],
    )


@router.get("/{meeting_id}/minutes", response_model=MinutesRead)
async def get_minutes(
    meeting_id: UUID,
    service: MeetingService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> MinutesRead:
    try:
        _meeting, minutes = await service.get_minutes(
            meeting_id, user_id=user_id
        )
    except MeetingNotFound:
        raise _not_found() from None
    except MeetingNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MinutesRead.model_validate(minutes)


@router.post("/{meeting_id}/minutes/regenerate", response_model=MinutesRead)
async def regenerate_minutes(
    meeting_id: UUID,
    service: MeetingService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> MinutesRead:
    try:
        minutes = await service.regenerate_minutes(meeting_id, user_id=user_id)
    except MeetingNotFound:
        raise _not_found() from None
    except MeetingNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MinutesRead.model_validate(minutes)


@router.get("/{meeting_id}/export")
async def export_meeting(
    meeting_id: UUID,
    fmt: Literal["markdown", "txt"] = Query(default="markdown", alias="format"),
    service: MeetingService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> Response:
    try:
        content, media_type, filename = await service.export(
            meeting_id, user_id=user_id, fmt=fmt
        )
    except MeetingNotFound:
        raise _not_found() from None
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{meeting_id}", status_code=204)
async def delete_meeting(
    meeting_id: UUID,
    service: MeetingService = Depends(get_service),
    user_id: str = Depends(get_user_id),
) -> Response:
    try:
        await service.delete_meeting(meeting_id, user_id=user_id)
    except MeetingNotFound:
        raise _not_found() from None
    return Response(status_code=204)
