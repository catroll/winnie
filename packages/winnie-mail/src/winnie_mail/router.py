"""The console's mailbox settings: read, save, test, send a test, list recent.

Every endpoint acts on the authenticated account's own file. A failure is a 400
when the owner can fix it (missing field, rejected password) and a 502 when the
mailbox could not be reached — never a 401, which the console would read as an
expired session.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from winnie_core.auth.dependencies import get_user_id
from winnie_mail.client import MailAuthError, MailError, MailNotConfigured
from winnie_mail.schemas import (
    MailboxView,
    MailMessageView,
    MailSendRequest,
    MailSendResult,
    MailTestRequest,
    MailTestResult,
    MailUpdate,
    MailView,
)
from winnie_mail.service import MailService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/mail", tags=["mail"])


def get_service(request: Request) -> MailService:
    service = getattr(request.app.state, "mail_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="mail settings are disabled")
    return service


def _http_error(exc: MailError) -> HTTPException:
    status = 400 if isinstance(exc, (MailNotConfigured, MailAuthError)) else 502
    return HTTPException(status_code=status, detail=str(exc))


@router.get("")
async def read_settings(
    request: Request,
    user_id: str = Depends(get_user_id),
) -> MailView:
    return get_service(request).view(user_id)


@router.put("")
async def save_settings(
    request: Request,
    payload: MailUpdate,
    user_id: str = Depends(get_user_id),
) -> MailView:
    return get_service(request).apply(user_id, payload)


@router.delete("")
async def clear_settings(
    request: Request,
    user_id: str = Depends(get_user_id),
) -> dict:
    return {"cleared": get_service(request).clear(user_id)}


@router.post("/test")
async def test_account(
    request: Request,
    payload: MailTestRequest,
    user_id: str = Depends(get_user_id),
) -> MailTestResult:
    try:
        detail = await get_service(request).test(user_id, payload.target)
    except MailError as exc:
        logger.info("mail test %s failed: %s", payload.target, exc)
        raise _http_error(exc) from exc
    return MailTestResult(ok=True, target=payload.target, detail=detail)


@router.post("/send")
async def send_message(
    request: Request,
    payload: MailSendRequest,
    user_id: str = Depends(get_user_id),
) -> MailSendResult:
    subject = payload.subject.strip() or "Winnie mail test"
    body = payload.body.strip() or (
        "This is a test message from Winnie. If it arrived, the mailbox is "
        "configured correctly."
    )
    try:
        receipt = await get_service(request).send(
            user_id, to=payload.to, subject=subject, body=body
        )
    except MailError as exc:
        logger.info("mail send failed: %s", exc)
        raise _http_error(exc) from exc
    return MailSendResult(ok=True, receipt=receipt)


@router.get("/messages")
async def recent_messages(
    request: Request,
    limit: int = Query(default=10, ge=1, le=100),
    user_id: str = Depends(get_user_id),
) -> MailboxView:
    service = get_service(request)
    settings = service.load(user_id)
    try:
        messages = await service.recent(user_id, limit=limit)
    except MailError as exc:
        logger.info("mail fetch failed: %s", exc)
        raise _http_error(exc) from exc
    return MailboxView(
        protocol=settings.receive.protocol,
        folder=settings.receive.folder or "INBOX",
        messages=[
            MailMessageView(
                uid=message.uid,
                subject=message.subject,
                sender=message.sender,
                to=message.to,
                date=message.date,
                snippet=message.snippet,
                body=message.body,
                unread=message.unread,
                message_id=message.message_id,
            )
            for message in messages
        ],
    )
