"""Durable push notifications plus the in-process live stream.

`publish` writes first and fans out second, so a device that is offline, or a
process that dies right after the write, still has the event in the backlog.
The cursor is `seq`, a monotonic integer: a client that reconnects sends the
last seq it saw and receives everything newer that has not expired.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_push.config import PushConfig
from winnie_push.hub import PushHub
from winnie_push.models import KINDS, PushNotification, as_utc, utcnow

logger = logging.getLogger(__name__)

MAX_TITLE = 200
MAX_BODY = 4000


class PushError(RuntimeError):
    """A push operation that cannot be completed for a reportable reason."""


def aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PushService:
    def __init__(
        self,
        config: PushConfig,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] | None = None,
        hub: PushHub | None = None,
    ) -> None:
        self.config = config
        self.session_factory = session_factory
        self.clock = clock or utcnow
        self.hub = hub or PushHub(max_queue=config.max_queue)
        self._prune_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        if self.config.prune_interval_seconds > 0:
            self._prune_task = asyncio.create_task(self._prune_loop())

    async def shutdown(self) -> None:
        if self._prune_task is not None:
            self._prune_task.cancel()
            try:
                await self._prune_task
            except asyncio.CancelledError:
                pass
            self._prune_task = None

    # -- publishing ------------------------------------------------------

    async def publish(
        self,
        user_id: str,
        *,
        title: str,
        body: str = "",
        kind: str = "notification",
        data: dict[str, Any] | None = None,
        device_id: uuid.UUID | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise PushError(f"unknown push kind: {kind}")
        now = self._now()
        if ttl_seconds is None and kind == "sync":
            # A "sync now" request is useless a few minutes later.
            ttl_seconds = 300
        expires_at = now + timedelta(seconds=ttl_seconds) if ttl_seconds else None

        async with self.session_factory() as session:
            try:
                event = PushNotification(
                    user_id=user_id,
                    device_id=device_id,
                    kind=kind,
                    title=(title or "")[:MAX_TITLE],
                    body=(body or "")[:MAX_BODY],
                    data=dict(data or {}),
                    created_at=now,
                    expires_at=expires_at,
                )
                session.add(event)
                await session.flush()
                view = self._view(event)
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
        delivered = self.hub.publish(user_id, view)
        logger.info(
            "push published user=%s kind=%s seq=%s device=%s delivered=%d",
            user_id,
            kind,
            view["seq"],
            device_id,
            delivered,
        )
        return view

    async def notify(
        self,
        user_id: str,
        *,
        title: str,
        body: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convenience for a user-facing notice."""
        return await self.publish(
            user_id, title=title, body=body, kind="notification", data=data
        )

    # -- reading ---------------------------------------------------------

    async def backlog(
        self,
        user_id: str,
        *,
        after_seq: int = 0,
        device_id: uuid.UUID | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        take = min(limit or self.config.max_backlog, self.config.max_backlog)
        now = self._now()
        async with self.session_factory() as session:
            conditions = [
                PushNotification.user_id == user_id,
                PushNotification.seq > int(after_seq),
                or_(
                    PushNotification.expires_at.is_(None),
                    PushNotification.expires_at > now,
                ),
            ]
            if device_id is None:
                conditions.append(PushNotification.device_id.is_(None))
            else:
                conditions.append(
                    or_(
                        PushNotification.device_id.is_(None),
                        PushNotification.device_id == device_id,
                    )
                )
            rows = await session.scalars(
                select(PushNotification)
                .where(*conditions)
                .order_by(PushNotification.seq.asc())
                .limit(take)
            )
            return [self._view(row) for row in rows]

    async def pending_count(self, user_id: str) -> int:
        now = self._now()
        async with self.session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(PushNotification)
                    .where(
                        PushNotification.user_id == user_id,
                        or_(
                            PushNotification.expires_at.is_(None),
                            PushNotification.expires_at > now,
                        ),
                    )
                )
                or 0
            )

    async def status(self, user_id: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(
                        func.max(PushNotification.created_at),
                        func.max(PushNotification.seq),
                    ).where(PushNotification.user_id == user_id)
                )
            ).one()
        last, last_seq = row
        return {
            "enabled": self.config.enabled,
            "connected": self.hub.connections(user_id),
            "pending": await self.pending_count(user_id),
            "last_event_at": aware_utc(last),
            "last_seq": int(last_seq or 0),
        }

    # -- retention -------------------------------------------------------

    async def prune(self, user_id: str | None = None) -> int:
        now = self._now()
        cutoff = now - timedelta(days=self.config.retention_days)
        conditions = [
            or_(
                PushNotification.expires_at < now,
                PushNotification.created_at < cutoff,
            )
        ]
        if user_id is not None:
            conditions.append(PushNotification.user_id == user_id)
        async with self.session_factory() as session:
            try:
                result = await session.execute(
                    delete(PushNotification).where(*conditions)
                )
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
        removed = int(result.rowcount or 0)
        if removed:
            logger.info("push retention removed %d rows", removed)
        return removed

    async def _prune_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.config.prune_interval_seconds)
                await self.prune()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - retention must not kill the API
                logger.exception("push retention pass failed")

    # -- helpers ---------------------------------------------------------

    def _now(self) -> datetime:
        value = self.clock()
        return as_utc(value) or utcnow()

    def _view(self, event: PushNotification) -> dict[str, Any]:
        return {
            "seq": int(event.seq),
            "id": str(event.id),
            "kind": event.kind,
            "title": event.title,
            "body": event.body,
            "data": dict(event.data or {}),
            "device_id": str(event.device_id) if event.device_id else None,
            "created_at": aware_utc(event.created_at).isoformat()
            if event.created_at
            else "",
            "expires_at": aware_utc(event.expires_at).isoformat()
            if event.expires_at
            else None,
        }
