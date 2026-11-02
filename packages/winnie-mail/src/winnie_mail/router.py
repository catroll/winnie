"""Two surfaces, one capability.

``/settings/mail`` is the account: read, save, test, forget, send a test.
``/mail`` is the cache: sync every folder, list folders, list and open messages.

Every endpoint acts on the authenticated account's own data. A failure is a 400
when the owner can fix it (missing field, rejected password), a 502 when the
mailbox could not be reached, and a 503 when the cache has no database — never a
401, which the console would read as an expired session.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from winnie_core.auth.dependencies import get_user_id
from winnie_mail.client import (
    MailAuthError,
    MailError,
    MailNotConfigured,
    MailUnavailable,
)
from winnie_mail.schemas import (
    MailCacheMessage,
    MailFolderList,
    MailSendRequest,
    MailSendResult,
    MailSyncResult,
    MailTestRequest,
    MailTestResult,
    MailUpdate,
    MailView,
)
from winnie_mail.service import MailService

logger = logging.getLogger(__name__)

settings_router = APIRouter(prefix="/settings/mail", tags=["mail"])
mailbox_router = APIRouter(prefix="/mail", tags=["mail"])

router = APIRouter()
router.include_router(settings_router)
router.include_router(mailbox_router)


def get_service(request: Request) -> MailService:
    service = getattr(request.app.state, "mail_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="mail is disabled")
    return service


def _http_error(exc: MailError) -> HTTPException:
    if isinstance(exc, (MailNotConfigured, MailAuthError)):
        status = 400
    elif isinstance(exc, MailUnavailable):
        status = 503
    else:
        status = 502
    return HTTPException(status_code=status, detail=str(exc))


# -- the account ---------------------------------------------------------


@settings_router.get("")
async def read_settings(
    request: Request,
    user_id: str = Depends(get_user_id),
) -> MailView:
    return get_service(request).view(user_id)


@settings_router.put("")
async def save_settings(
    request: Request,
    payload: MailUpdate,
    user_id: str = Depends(get_user_id),
) -> MailView:
    return get_service(request).apply(user_id, payload)


@settings_router.delete("")
async def clear_settings(
    request: Request,
    user_id: str = Depends(get_user_id),
) -> dict:
    return {"cleared": await get_service(request).clear(user_id)}


@settings_router.post("/test")
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


@settings_router.post("/send")
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


# -- the cache -----------------------------------------------------------


@mailbox_router.post("/sync")
async def sync_mailbox(
    request: Request,
    user_id: str = Depends(get_user_id),
) -> MailSyncResult:
    try:
        result = await get_service(request).sync(user_id)
    except MailError as exc:
        logger.info("mail sync failed: %s", exc)
        raise _http_error(exc) from exc
    logger.info(
        "mail sync user=%s protocol=%s folders=%s fetched=%s errors=%s",
        user_id,
        result["protocol"],
        result["folders"],
        result["fetched"],
        len(result["errors"]),
    )
    return MailSyncResult(**result)


@mailbox_router.get("/folders")
async def list_folders(
    request: Request,
    user_id: str = Depends(get_user_id),
) -> MailFolderList:
    try:
        return MailFolderList(**await get_service(request).folders(user_id))
    except MailError as exc:
        raise _http_error(exc) from exc


@mailbox_router.get("/messages")
async def list_messages(
    request: Request,
    folder: str = Query(default="", max_length=255),
    q: str = Query(default="", max_length=200),
    unread: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_user_id),
) -> list[MailCacheMessage]:
    try:
        rows = await get_service(request).messages(
            user_id,
            folder=folder,
            query=q,
            unread=unread,
            limit=limit,
            offset=offset,
        )
    except MailError as exc:
        raise _http_error(exc) from exc
    return [MailCacheMessage(**row) for row in rows]


@mailbox_router.get("/messages/{message_id}")
async def read_message(
    request: Request,
    message_id: str,
    user_id: str = Depends(get_user_id),
) -> MailCacheMessage:
    try:
        row = await get_service(request).message(user_id, message_id)
    except MailError as exc:
        logger.info("mail read failed: %s", exc)
        raise _http_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="no such message in the cache")
    return MailCacheMessage(**row)
