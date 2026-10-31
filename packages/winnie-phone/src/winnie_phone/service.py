"""Phone-data business logic: sync, query, search and deferred writes.

The phone is the writer for its own data; the server only merges batches keyed
by ``(device, kind, external_id)``. Nothing in this module talks HTTP, so the
assistant tools, the router and the tests all use the same operations.

Writes flow the other way: the assistant proposes a command, the owner confirms
it in chat, and the phone claims it on its next sync. A proposed command that is
never confirmed expires and is never dispatched.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_phone.config import PhoneConfig
from winnie_phone.models import (
    PhoneCalendarEvent,
    PhoneCall,
    PhoneCommand,
    PhoneContact,
    PhoneDevice,
    PhoneMessage,
    PhoneSignal,
    PhoneSyncState,
    as_utc,
    utcnow,
)
from winnie_phone.schemas import (
    ITEM_MODELS,
    PAYLOAD_MODELS,
    DeviceRegister,
    SyncRequest,
)

logger = logging.getLogger(__name__)

# Kinds stored as their own table, with the fields that need normalising.
# Contacts, calls and calendar rows are joined on and filtered by time; the
# ambient kinds share PhoneSignal instead.
@dataclass(frozen=True)
class RecordSpec:
    model: type
    aliases: dict[str, str]
    time_fields: tuple[str, ...]


RECORD_SPECS: dict[str, RecordSpec] = {
    "calendar": RecordSpec(
        PhoneCalendarEvent,
        aliases={"updated_at": "external_updated_at"},
        time_fields=("starts_at", "ends_at", "external_updated_at"),
    ),
    "call": RecordSpec(
        PhoneCall,
        aliases={},
        time_fields=("started_at",),
    ),
    "message": RecordSpec(
        PhoneMessage,
        aliases={},
        time_fields=("received_at",),
    ),
    "contact": RecordSpec(
        PhoneContact,
        aliases={},
        time_fields=(),
    ),
}

SIGNAL_KINDS = frozenset({"notification", "location", "app"})
# Kinds whose "full" batch is a complete current view, so the server can mark
# anything it holds but the batch omits as deleted. A paged snapshot must not
# use full mode; the client diffs explicit ids instead.
FULL_SNAPSHOT_KINDS = frozenset({"calendar", "contact", "app", "device"})
COMMAND_KINDS = frozenset(PAYLOAD_MODELS)
COMMAND_STATUSES = (
    "proposed",
    "approved",
    "dispatched",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
)
# Record kinds the assistant searches over. ``device`` is not searchable.
SEARCH_KINDS = (
    "calendar",
    "call",
    "message",
    "contact",
    "notification",
    "location",
    "app",
)


class PhoneEvents(Protocol):
    """What the host is told when a command changes state.

    The phone package does not know about push or notifications; the host
    implements this to wake the device and to tell the owner the outcome.
    Implementations must not raise: a delivery failure is not an API failure.
    """

    async def command_approved(
        self, user_id: str, device_id: uuid.UUID, command: dict
    ) -> None: ...

    async def command_completed(
        self, user_id: str, device_id: uuid.UUID, command: dict
    ) -> None: ...


class PhoneError(RuntimeError):
    """A phone-data operation that cannot be completed for a reportable reason."""


class DeviceNotFound(PhoneError):
    pass


class CommandNotFound(PhoneError):
    pass


class InvalidCommand(PhoneError):
    pass


class DeviceRevoked(PhoneError):
    pass


def aware_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a stored naive value so JSON carries an offset."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _truncate(value: str | None, limit: int) -> str:
    if not value:
        return ""
    return value if len(value) <= limit else value[:limit]


class PhoneService:
    def __init__(
        self,
        config: PhoneConfig,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] | None = None,
        events: PhoneEvents | None = None,
    ) -> None:
        self.config = config
        self.session_factory = session_factory
        # Naive UTC everywhere; see models.as_utc.
        self.clock = clock or utcnow
        self.events = events

    # -- plumbing --------------------------------------------------------

    @asynccontextmanager
    async def _session(self):
        session = self.session_factory()
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        finally:
            await session.close()

    def _now(self) -> datetime:
        return as_utc(self.clock()) or utcnow()

    # -- devices ---------------------------------------------------------

    async def register_device(self, user_id: str, payload: DeviceRegister) -> dict:
        now = self._now()
        async with self._session() as session:
            device = await session.scalar(
                select(PhoneDevice).where(
                    PhoneDevice.user_id == user_id,
                    PhoneDevice.device_key == payload.device_key,
                )
            )
            if device is None:
                device = PhoneDevice(
                    user_id=user_id,
                    device_key=payload.device_key,
                    platform=payload.platform,
                )
                session.add(device)
            elif device.revoked:
                raise DeviceRevoked(
                    "this phone was revoked; restore it before it can sync again"
                )
            device.name = payload.name or payload.model or "Android phone"
            device.platform = payload.platform
            device.manufacturer = payload.manufacturer
            device.model = payload.model
            device.os_version = payload.os_version
            device.app_version = payload.app_version
            device.timezone = payload.timezone
            device.scopes = list(dict.fromkeys(payload.scopes))
            device.info = _merged_info(device.info, payload.info)
            device.last_seen_at = now
            await session.flush()
            states = await self._states(session, device.id)
            view = self._device_view(device, states)
            logger.info(
                "phone device registered user=%s device=%s name=%s scopes=%s",
                user_id,
                device.id,
                device.name,
                ",".join(device.scopes),
            )
            return view

    async def list_devices(self, user_id: str) -> list[dict]:
        async with self._session() as session:
            devices = list(
                await session.scalars(
                    select(PhoneDevice)
                    .where(PhoneDevice.user_id == user_id)
                    .order_by(PhoneDevice.last_seen_at.desc().nullslast())
                )
            )
            return [
                self._device_view(device, await self._states(session, device.id))
                for device in devices
            ]

    async def get_device(self, user_id: str, device_id: uuid.UUID) -> dict:
        async with self._session() as session:
            device = await self._require_device(session, user_id, device_id)
            return self._device_view(
                device, await self._states(session, device.id)
            )

    async def _require_device(
        self,
        session: AsyncSession,
        user_id: str,
        device_id: uuid.UUID | None,
    ) -> PhoneDevice:
        if device_id is None:
            device = await session.scalar(
                select(PhoneDevice)
                .where(
                    PhoneDevice.user_id == user_id,
                    PhoneDevice.revoked.is_(False),
                )
                .order_by(PhoneDevice.last_seen_at.desc().nullslast())
                .limit(1)
            )
        else:
            device = await session.scalar(
                select(PhoneDevice).where(
                    PhoneDevice.id == device_id,
                    PhoneDevice.user_id == user_id,
                )
            )
        if device is None:
            raise DeviceNotFound("no phone has been registered for this account")
        if device.revoked:
            raise DeviceRevoked("this phone was revoked by the server")
        return device

    async def revoke_device(self, user_id: str, device_id: uuid.UUID) -> None:
        async with self._session() as session:
            device = await self._require_device(session, user_id, device_id)
            device.revoked = True

    async def restore_device(self, user_id: str, device_id: uuid.UUID) -> dict:
        """Undo a revocation. Deliberately explicit: revocation is sticky."""
        async with self._session() as session:
            device = await session.scalar(
                select(PhoneDevice).where(
                    PhoneDevice.id == device_id,
                    PhoneDevice.user_id == user_id,
                )
            )
            if device is None:
                raise DeviceNotFound("no phone has been registered for this account")
            device.revoked = False
            return self._device_view(
                device, await self._states(session, device.id)
            )

    async def _states(
        self, session: AsyncSession, device_id: uuid.UUID
    ) -> list[PhoneSyncState]:
        return list(
            await session.scalars(
                select(PhoneSyncState)
                .where(PhoneSyncState.device_id == device_id)
                .order_by(PhoneSyncState.kind)
            )
        )

    def _device_view(
        self, device: PhoneDevice, states: list[PhoneSyncState]
    ) -> dict:
        return {
            "id": device.id,
            "device_key": device.device_key,
            "name": device.name,
            "platform": device.platform,
            "manufacturer": device.manufacturer,
            "model": device.model,
            "os_version": device.os_version,
            "app_version": device.app_version,
            "timezone": device.timezone,
            "scopes": list(device.scopes or []),
            "info": dict(device.info or {}),
            "revoked": device.revoked,
            "last_seen_at": aware_utc(device.last_seen_at),
            "last_sync_at": aware_utc(device.last_sync_at),
            "created_at": aware_utc(device.created_at),
            "states": [
                {
                    "kind": state.kind,
                    "cursor": state.cursor,
                    "last_sync_at": aware_utc(state.last_sync_at),
                    "full_sync_at": aware_utc(state.full_sync_at),
                    "last_error": state.last_error,
                    "accepted_total": state.accepted_total,
                }
                for state in states
            ],
        }

    # -- sync ------------------------------------------------------------

    async def sync(
        self,
        user_id: str,
        device_id: uuid.UUID,
        request: SyncRequest,
    ) -> dict:
        kind = request.kind
        if len(request.items) > self.config.max_batch_items:
            raise PhoneError(
                f"batch has {len(request.items)} items; the limit is "
                f"{self.config.max_batch_items}"
            )
        if request.mode == "full" and kind not in FULL_SNAPSHOT_KINDS:
            raise PhoneError(
                f"a full snapshot is not supported for {kind}; "
                "send deletions explicitly"
            )
        validated = [ITEM_MODELS[kind].model_validate(item) for item in request.items]
        window = (as_utc(request.since), as_utc(request.until))
        if window == (None, None):
            window = None

        async with self._session() as session:
            device = await self._require_device(session, user_id, device_id)
            now = self._now()
            if request.device is not None:
                self._apply_device_snapshot(device, request.device)
            device.last_seen_at = now

            if kind == "device":
                # The device snapshot already travelled in ``request.device``;
                # there is no history to merge for this kind.
                accepted = 1 if request.device is not None else 0
                deleted = 0
            elif kind in SIGNAL_KINDS:
                accepted, deleted = await self._sync_signals(
                    session,
                    device,
                    user_id,
                    kind,
                    validated,
                    request.deleted,
                    full=request.mode == "full",
                    now=now,
                )
            else:
                accepted, deleted = await self._sync_records(
                    session,
                    device,
                    user_id,
                    kind,
                    validated,
                    request.deleted,
                    full=request.mode == "full",
                    now=now,
                    window=window if kind == "calendar" else None,
                )

            state = await self._state_for(session, device.id, kind)
            state.cursor = request.cursor[:64]
            state.last_sync_at = now
            state.accepted_total = int(state.accepted_total or 0) + accepted
            state.last_error = ""
            if request.mode == "full":
                state.full_sync_at = now
            device.last_sync_at = now
            await self.purge_in_session(session, user_id=user_id, device=device)
            await self.expire_commands_in_session(session, user_id=user_id)

            logger.info(
                "phone sync user=%s device=%s kind=%s mode=%s accepted=%d deleted=%d",
                user_id,
                device.id,
                kind,
                request.mode,
                accepted,
                deleted,
            )
            return {
                "kind": kind,
                "accepted": accepted,
                "deleted": deleted,
                "cursor": request.cursor[:64],
                "server_time": aware_utc(now),
                "interval_minutes": self.config.sync_interval_minutes,
            }

    def _apply_device_snapshot(self, device: PhoneDevice, snapshot) -> None:
        device.name = snapshot.name or device.name
        device.manufacturer = snapshot.manufacturer or device.manufacturer
        device.model = snapshot.model or device.model
        device.os_version = snapshot.os_version or device.os_version
        device.app_version = snapshot.app_version or device.app_version
        device.timezone = snapshot.timezone or device.timezone
        if snapshot.scopes:
            device.scopes = list(dict.fromkeys(snapshot.scopes))
        device.info = _merged_info(device.info, snapshot.info)

    async def _state_for(
        self, session: AsyncSession, device_id: uuid.UUID, kind: str
    ) -> PhoneSyncState:
        state = await session.scalar(
            select(PhoneSyncState).where(
                PhoneSyncState.device_id == device_id,
                PhoneSyncState.kind == kind,
            )
        )
        if state is None:
            state = PhoneSyncState(device_id=device_id, kind=kind)
            session.add(state)
            await session.flush()
        return state

    async def _sync_records(
        self,
        session: AsyncSession,
        device: PhoneDevice,
        user_id: str,
        kind: str,
        items: list[Any],
        deleted: list[str],
        *,
        full: bool,
        now: datetime,
        window: tuple[datetime | None, datetime | None] | None = None,
    ) -> tuple[int, int]:
        spec = RECORD_SPECS[kind]
        model = spec.model
        rows = list(
            await session.scalars(
                select(model).where(model.device_id == device.id)
            )
        )
        existing = {row.external_id: row for row in rows}
        seen: set[str] = set()
        accepted = 0
        for item in items:
            data = item.model_dump()
            external_id = data.pop("external_id")
            seen.add(external_id)
            values: dict[str, Any] = {}
            for key, value in data.items():
                column = spec.aliases.get(key, key)
                if column in spec.time_fields:
                    value = as_utc(value)
                elif isinstance(value, str):
                    value = _truncate(value, self.config.max_text_chars)
                values[column] = value
            row = existing.get(external_id)
            if row is None:
                row = model(
                    user_id=user_id,
                    device_id=device.id,
                    external_id=external_id[:64],
                    **values,
                )
                session.add(row)
                existing[external_id] = row
            else:
                for column, value in values.items():
                    setattr(row, column, value)
                row.deleted_at = None
            accepted += 1
        changed = await self._apply_deletions(
            session,
            existing,
            deleted,
            seen if full else None,
            now,
            window_column=PhoneCalendarEvent.starts_at if kind == "calendar" else None,
            window=window,
        )
        return accepted, changed

    async def _sync_signals(
        self,
        session: AsyncSession,
        device: PhoneDevice,
        user_id: str,
        kind: str,
        items: list[Any],
        deleted: list[str],
        *,
        full: bool,
        now: datetime,
    ) -> tuple[int, int]:
        rows = list(
            await session.scalars(
                select(PhoneSignal).where(
                    PhoneSignal.device_id == device.id,
                    PhoneSignal.kind == kind,
                )
            )
        )
        existing = {row.external_id: row for row in rows}
        seen: set[str] = set()
        accepted = 0
        for item in items:
            values = _signal_values(kind, item, self.config.max_text_chars)
            external_id = values.pop("external_id")
            seen.add(external_id)
            row = existing.get(external_id)
            if row is None:
                row = PhoneSignal(
                    user_id=user_id,
                    device_id=device.id,
                    kind=kind,
                    external_id=external_id[:128],
                    **values,
                )
                session.add(row)
                existing[external_id] = row
            else:
                for column, value in values.items():
                    setattr(row, column, value)
                row.deleted_at = None
            accepted += 1
        changed = await self._apply_deletions(
            session, existing, deleted, seen if full else None, now
        )
        return accepted, changed

    async def _apply_deletions(
        self,
        session: AsyncSession,
        existing: dict[str, Any],
        deleted: list[str],
        snapshot: set[str] | None,
        now: datetime,
        *,
        window_column: Any = None,
        window: tuple[datetime | None, datetime | None] | None = None,
    ) -> int:
        changed = 0
        explicit = set(deleted)
        since, until = window or (None, None)
        for external_id, row in existing.items():
            if row.deleted_at is not None:
                continue
            if external_id in explicit:
                row.deleted_at = now
                changed += 1
                continue
            if snapshot is None or external_id in snapshot:
                continue
            if window_column is not None and (since is not None or until is not None):
                value = getattr(row, "starts_at", None)
                if value is None:
                    continue
                if since is not None and value < since:
                    continue
                if until is not None and value > until:
                    continue
            row.deleted_at = now
            changed += 1
        return changed

    async def external_ids(
        self,
        user_id: str,
        device_id: uuid.UUID,
        kind: str,
        *,
        limit: int = 5000,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[str]:
        """Ids the server currently holds for one device and kind."""
        async with self._session() as session:
            device = await self._require_device(session, user_id, device_id)
            if kind in RECORD_SPECS:
                model = RECORD_SPECS[kind].model
                condition = [model.device_id == device.id, model.deleted_at.is_(None)]
                if kind == "calendar":
                    if since is not None:
                        condition.append(model.starts_at >= as_utc(since))
                    if until is not None:
                        condition.append(model.starts_at <= as_utc(until))
                rows = await session.scalars(
                    select(model.external_id).where(*condition).limit(limit)
                )
                return list(rows)
            if kind in SIGNAL_KINDS:
                rows = await session.scalars(
                    select(PhoneSignal.external_id)
                    .where(
                        PhoneSignal.device_id == device.id,
                        PhoneSignal.kind == kind,
                        PhoneSignal.deleted_at.is_(None),
                    )
                    .limit(limit)
                )
                return list(rows)
            raise PhoneError(f"unsupported kind: {kind}")

    # -- queries ---------------------------------------------------------

    async def counts(self, user_id: str, device_id: uuid.UUID | None = None) -> dict[str, int]:
        async with self._session() as session:
            return await self._counts(session, user_id, device_id)

    async def _counts(
        self,
        session: AsyncSession,
        user_id: str,
        device_id: uuid.UUID | None = None,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        record_models = {
            "calendar": PhoneCalendarEvent,
            "call": PhoneCall,
            "message": PhoneMessage,
            "contact": PhoneContact,
        }
        for kind, model in record_models.items():
            condition = [model.user_id == user_id, model.deleted_at.is_(None)]
            if device_id is not None:
                condition.append(model.device_id == device_id)
            counts[kind] = int(
                await session.scalar(
                    select(func.count()).select_from(model).where(*condition)
                )
                or 0
            )
        for kind in ("notification", "location", "app"):
            condition = [
                PhoneSignal.user_id == user_id,
                PhoneSignal.kind == kind,
                PhoneSignal.deleted_at.is_(None),
            ]
            if device_id is not None:
                condition.append(PhoneSignal.device_id == device_id)
            counts[kind] = int(
                await session.scalar(
                    select(func.count()).select_from(PhoneSignal).where(*condition)
                )
                or 0
            )
        return counts

    async def summary(self, user_id: str) -> dict:
        async with self._session() as session:
            devices = list(
                await session.scalars(
                    select(PhoneDevice)
                    .where(
                        PhoneDevice.user_id == user_id,
                        PhoneDevice.revoked.is_(False),
                    )
                    .order_by(PhoneDevice.last_seen_at.desc().nullslast())
                )
            )
            await self.expire_commands_in_session(session, user_id=user_id)
            pending = int(
                await session.scalar(
                    select(func.count())
                    .select_from(PhoneCommand)
                    .where(
                        PhoneCommand.user_id == user_id,
                        PhoneCommand.status.in_(("proposed", "approved")),
                    )
                )
                or 0
            )
            device_views = []
            totals: dict[str, int] = {kind: 0 for kind in SEARCH_KINDS}
            for device in devices:
                states = await self._states(session, device.id)
                counts = await self._counts(session, user_id, device.id)
                for kind, value in counts.items():
                    totals[kind] = totals.get(kind, 0) + value
                device_views.append(
                    {
                        "device": self._device_view(device, states),
                        "counts": [
                            {"kind": kind, "count": value}
                            for kind, value in counts.items()
                        ],
                    }
                )
            last_sync = max(
                (device.last_sync_at for device in devices if device.last_sync_at),
                default=None,
            )
            now = self._now()
            stale = (
                int((now - as_utc(last_sync)).total_seconds() // 60)
                if last_sync is not None
                else None
            )
            return {
                "devices": device_views,
                "counts": [
                    {"kind": kind, "count": value}
                    for kind, value in totals.items()
                ],
                "last_sync_at": aware_utc(last_sync),
                "stale_minutes": stale,
                "pending_commands": pending,
            }

    async def calendar(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        query: str = "",
        device_id: uuid.UUID | None = None,
        limit: int = 50,
        ascending: bool = True,
    ) -> list[dict]:
        async with self._session() as session:
            names = await self._device_names(session, user_id)
            conditions = [
                PhoneCalendarEvent.user_id == user_id,
                PhoneCalendarEvent.deleted_at.is_(None),
            ]
            if device_id is not None:
                conditions.append(PhoneCalendarEvent.device_id == device_id)
            if since is not None:
                conditions.append(PhoneCalendarEvent.starts_at >= as_utc(since))
            if until is not None:
                conditions.append(PhoneCalendarEvent.starts_at <= as_utc(until))
            if query:
                conditions.append(
                    or_(
                        PhoneCalendarEvent.title.ilike(_like(query), escape="\\"),
                        PhoneCalendarEvent.description.ilike(_like(query), escape="\\"),
                        PhoneCalendarEvent.location.ilike(_like(query), escape="\\"),
                    )
                )
            column = PhoneCalendarEvent.starts_at
            order = column.asc() if ascending else column.desc()
            rows = await session.scalars(
                select(PhoneCalendarEvent)
                .where(*conditions)
                .order_by(order.nullslast())
                .limit(limit)
            )
            return [
                {
                    "id": row.external_id,
                    "device_id": str(row.device_id),
                    "device_name": names.get(row.device_id, "phone"),
                    "calendar_name": row.calendar_name,
                    "title": row.title,
                    "description": row.description,
                    "location": row.location,
                    "starts_at": aware_utc(row.starts_at),
                    "ends_at": aware_utc(row.ends_at),
                    "all_day": row.all_day,
                    "timezone": row.timezone,
                    "recurrence": row.recurrence,
                    "organizer": row.organizer,
                    "attendees": list(row.attendees or []),
                    "status": row.status,
                }
                for row in rows
            ]

    async def calls(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        direction: str = "",
        query: str = "",
        device_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[dict]:
        async with self._session() as session:
            names = await self._device_names(session, user_id)
            conditions = [
                PhoneCall.user_id == user_id,
                PhoneCall.deleted_at.is_(None),
            ]
            if device_id is not None:
                conditions.append(PhoneCall.device_id == device_id)
            if since is not None:
                conditions.append(PhoneCall.started_at >= as_utc(since))
            if until is not None:
                conditions.append(PhoneCall.started_at <= as_utc(until))
            if direction:
                conditions.append(PhoneCall.direction == direction)
            if query:
                conditions.append(
                    or_(
                        PhoneCall.number.ilike(_like(query), escape="\\"),
                        PhoneCall.cached_name.ilike(_like(query), escape="\\"),
                    )
                )
            rows = await session.scalars(
                select(PhoneCall)
                .where(*conditions)
                .order_by(PhoneCall.started_at.desc().nullslast())
                .limit(limit)
            )
            return [
                {
                    "id": row.external_id,
                    "device_id": str(row.device_id),
                    "device_name": names.get(row.device_id, "phone"),
                    "number": row.number,
                    "cached_name": row.cached_name,
                    "direction": row.direction,
                    "started_at": aware_utc(row.started_at),
                    "duration_seconds": row.duration_seconds,
                    "is_new": row.is_new,
                }
                for row in rows
            ]

    async def messages(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        address: str = "",
        query: str = "",
        unread_only: bool = False,
        device_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[dict]:
        async with self._session() as session:
            names = await self._device_names(session, user_id)
            conditions = [
                PhoneMessage.user_id == user_id,
                PhoneMessage.deleted_at.is_(None),
            ]
            if device_id is not None:
                conditions.append(PhoneMessage.device_id == device_id)
            if since is not None:
                conditions.append(PhoneMessage.received_at >= as_utc(since))
            if until is not None:
                conditions.append(PhoneMessage.received_at <= as_utc(until))
            if address:
                conditions.append(
                    PhoneMessage.address.ilike(_like(address), escape="\\")
                )
            if query:
                conditions.append(
                    or_(
                        PhoneMessage.body.ilike(_like(query), escape="\\"),
                        PhoneMessage.address.ilike(_like(query), escape="\\"),
                    )
                )
            if unread_only:
                conditions.append(PhoneMessage.is_read.is_(False))
            rows = await session.scalars(
                select(PhoneMessage)
                .where(*conditions)
                .order_by(PhoneMessage.received_at.desc().nullslast())
                .limit(limit)
            )
            return [
                {
                    "id": row.external_id,
                    "device_id": str(row.device_id),
                    "device_name": names.get(row.device_id, "phone"),
                    "address": row.address,
                    "direction": row.direction,
                    "body": row.body,
                    "subject": row.subject,
                    "received_at": aware_utc(row.received_at),
                    "is_read": row.is_read,
                    "kind": row.kind,
                }
                for row in rows
            ]

    async def contacts(
        self,
        user_id: str,
        *,
        query: str = "",
        device_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[dict]:
        async with self._session() as session:
            names = await self._device_names(session, user_id)
            conditions = [
                PhoneContact.user_id == user_id,
                PhoneContact.deleted_at.is_(None),
            ]
            if device_id is not None:
                conditions.append(PhoneContact.device_id == device_id)
            if query:
                conditions.append(
                    or_(
                        PhoneContact.display_name.ilike(_like(query), escape="\\"),
                        PhoneContact.organization.ilike(_like(query), escape="\\"),
                    )
                )
            rows = await session.scalars(
                select(PhoneContact)
                .where(*conditions)
                .order_by(PhoneContact.display_name.asc())
                .limit(limit)
            )
            return [
                {
                    "id": row.external_id,
                    "device_id": str(row.device_id),
                    "device_name": names.get(row.device_id, "phone"),
                    "display_name": row.display_name,
                    "numbers": list(row.numbers or []),
                    "emails": list(row.emails or []),
                    "organization": row.organization,
                    "starred": row.starred,
                }
                for row in rows
            ]

    async def notifications(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        app: str = "",
        query: str = "",
        device_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[dict]:
        rows = await self._signals(
            user_id,
            "notification",
            since=since,
            until=until,
            query=query,
            extra=_signal_app_condition(app),
            device_id=device_id,
            limit=limit,
        )
        return [
            {
                "id": row.external_id,
                "device_id": str(row.device_id),
                "device_name": name,
                "package_name": (row.payload or {}).get("package", ""),
                "app_label": row.label,
                "title": (row.payload or {}).get("title", ""),
                "text": (row.payload or {}).get("text", ""),
                "category": (row.payload or {}).get("category", ""),
                "posted_at": aware_utc(row.occurred_at),
                "is_ongoing": bool((row.payload or {}).get("ongoing", False)),
            }
            for row, name in rows
        ]

    async def locations(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        device_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[dict]:
        rows = await self._signals(
            user_id,
            "location",
            since=since,
            until=until,
            device_id=device_id,
            limit=limit,
        )
        return [
            {
                "id": row.external_id,
                "device_id": str(row.device_id),
                "device_name": name,
                "latitude": (row.payload or {}).get("latitude", 0.0),
                "longitude": (row.payload or {}).get("longitude", 0.0),
                "accuracy_m": (row.payload or {}).get("accuracy_m"),
                "provider": row.label,
                "captured_at": aware_utc(row.occurred_at),
            }
            for row, name in rows
        ]

    async def apps(
        self,
        user_id: str,
        *,
        query: str = "",
        device_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[dict]:
        rows = await self._signals(
            user_id,
            "app",
            query=query,
            device_id=device_id,
            limit=limit,
        )
        return [
            {
                "id": row.external_id,
                "device_id": str(row.device_id),
                "device_name": name,
                "package_name": (row.payload or {}).get("package", row.external_id),
                "label": row.label,
                "version_name": (row.payload or {}).get("version_name", ""),
                "is_system": bool((row.payload or {}).get("is_system", False)),
                "is_enabled": bool((row.payload or {}).get("is_enabled", True)),
                "installed_at": aware_utc(
                    _parse_iso((row.payload or {}).get("installed_at", ""))
                ),
                "updated_at": aware_utc(
                    _parse_iso((row.payload or {}).get("updated_at", ""))
                ),
            }
            for row, name in rows
        ]

    async def _signals(
        self,
        user_id: str,
        kind: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        query: str = "",
        extra: Any = None,
        device_id: uuid.UUID | None = None,
        limit: int = 100,
        ascending: bool = False,
    ):
        async with self._session() as session:
            names = await self._device_names(session, user_id)
            conditions = [
                PhoneSignal.user_id == user_id,
                PhoneSignal.kind == kind,
                PhoneSignal.deleted_at.is_(None),
            ]
            if device_id is not None:
                conditions.append(PhoneSignal.device_id == device_id)
            if since is not None:
                conditions.append(PhoneSignal.occurred_at >= as_utc(since))
            if until is not None:
                conditions.append(PhoneSignal.occurred_at <= as_utc(until))
            if query:
                conditions.append(
                    or_(
                        PhoneSignal.label.ilike(_like(query), escape="\\"),
                        PhoneSignal.body.ilike(_like(query), escape="\\"),
                    )
                )
            if extra is not None:
                conditions.append(extra)
            column = PhoneSignal.occurred_at
            order = column.asc() if ascending else column.desc()
            rows = list(
                await session.scalars(
                    select(PhoneSignal)
                    .where(*conditions)
                    .order_by(order.nullslast())
                    .limit(limit)
                )
            )
            return [
                (row, names.get(row.device_id, "phone")) for row in rows
            ]

    async def _device_names(
        self, session: AsyncSession, user_id: str
    ) -> dict[uuid.UUID, str]:
        rows = await session.execute(
            select(PhoneDevice.id, PhoneDevice.name, PhoneDevice.model).where(
                PhoneDevice.user_id == user_id
            )
        )
        return {
            device_id: (name or model or "phone")
            for device_id, name, model in rows
        }

    async def search(
        self,
        user_id: str,
        query: str,
        *,
        kinds: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search every kind and merge by time.

        Each kind is queried with its own columns, then the merged list is
        ordered by time and truncated. Phone datasets are small enough that
        this is cheaper and far clearer than a UNION over four tables and one
        JSON column.
        """
        if not query.strip():
            raise PhoneError("search needs a non-empty query")
        selected = [kind for kind in (kinds or SEARCH_KINDS) if kind in SEARCH_KINDS]
        per_kind = max(limit, 5)
        hits: list[dict] = []
        if "calendar" in selected:
            for row in await self.calendar(
                user_id, since=since, until=until, query=query, limit=per_kind
            ):
                hits.append(
                    _hit(
                        "calendar",
                        row["starts_at"],
                        f"{row['title'] or '(untitled event)'}",
                        _join_text(row["location"], row["description"]),
                        row,
                    )
                )
        if "call" in selected:
            for row in await self.calls(
                user_id, since=since, until=until, query=query, limit=per_kind
            ):
                who = row["cached_name"] or row["number"] or "unknown number"
                hits.append(
                    _hit(
                        "call",
                        row["started_at"],
                        f"{row['direction']} call with {who}",
                        f"{row['duration_seconds']}s",
                        row,
                    )
                )
        if "message" in selected:
            for row in await self.messages(
                user_id, since=since, until=until, query=query, limit=per_kind
            ):
                who = row["address"] or "unknown number"
                hits.append(
                    _hit(
                        "message",
                        row["received_at"],
                        f"SMS {row['direction']} with {who}",
                        row["body"],
                        row,
                    )
                )
        if "contact" in selected:
            for row in await self.contacts(
                user_id, query=query, limit=per_kind
            ):
                hits.append(
                    _hit(
                        "contact",
                        None,
                        f"contact {row['display_name']}",
                        _join_text(
                            ", ".join(row["numbers"]),
                            row["organization"],
                        ),
                        row,
                    )
                )
        if "notification" in selected:
            for row in await self.notifications(
                user_id, since=since, until=until, query=query, limit=per_kind
            ):
                hits.append(
                    _hit(
                        "notification",
                        row["posted_at"],
                        f"{row['app_label']}: {row['title']}",
                        row["text"],
                        row,
                    )
                )
        if "location" in selected:
            # A location row has no prose: it can only match a coordinate or a
            # provider name. Including every fix in an unrelated text search
            # would bury the record the owner asked about.
            needle = query.strip().lower()
            for row in await self.locations(
                user_id, since=since, until=until, limit=per_kind
            ):
                haystack = (
                    f"{row['latitude']:.5f},{row['longitude']:.5f} "
                    f"{row['provider']}"
                ).lower()
                if needle not in haystack:
                    continue
                hits.append(
                    _hit(
                        "location",
                        row["captured_at"],
                        f"location {row['latitude']:.5f},{row['longitude']:.5f}",
                        f"accuracy {row['accuracy_m'] or 0:.0f}m via {row['provider']}",
                        row,
                    )
                )
        if "app" in selected:
            for row in await self.apps(
                user_id, query=query, limit=per_kind
            ):
                hits.append(
                    _hit(
                        "app",
                        row["updated_at"],
                        f"app {row['label']}",
                        row["package_name"],
                        row,
                    )
                )
        hits.sort(
            key=lambda hit: hit["at"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return hits[:limit]

    # -- commands --------------------------------------------------------

    async def propose_command(
        self,
        user_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        device_id: uuid.UUID | None = None,
        summary: str = "",
        expires_in: int | None = None,
    ) -> dict:
        if kind not in COMMAND_KINDS:
            raise InvalidCommand(f"unsupported command kind: {kind}")
        model = PAYLOAD_MODELS[kind]
        try:
            validated = model.model_validate(payload)
        except Exception as exc:
            raise InvalidCommand(str(exc)) from exc
        data = validated.model_dump(mode="json")
        if kind == "calendar.create" and not data.get("ends_at"):
            start = datetime.fromisoformat(data["starts_at"])
            data["ends_at"] = (start + timedelta(hours=1)).isoformat()

        now = self._now()
        ttl = expires_in if expires_in is not None else self.config.command_ttl_seconds
        async with self._session() as session:
            device = await self._require_device(session, user_id, device_id)
            command = PhoneCommand(
                user_id=user_id,
                device_id=device.id,
                kind=kind,
                payload=data,
                summary=summary or self._command_summary(kind, data),
                status="proposed",
                expires_at=now + timedelta(seconds=ttl),
            )
            session.add(command)
            await session.flush()
            return self._command_view(command)

    async def approve_command(self, user_id: str, command_id: uuid.UUID) -> dict:
        async with self._session() as session:
            command = await self._require_command(session, user_id, command_id)
            now = self._now()
            if command.status == "proposed":
                if command.expires_at is not None and as_utc(command.expires_at) < now:
                    command.status = "expired"
                    raise InvalidCommand("this command expired; propose it again")
                command.status = "approved"
                command.approved_at = now
            elif command.status in {"approved", "dispatched"}:
                pass
            elif command.status in {"succeeded", "failed", "cancelled", "expired"}:
                raise InvalidCommand(f"command is already {command.status}")
            view = self._command_view(command)
            device_id = command.device_id
        await self._emit("approved", user_id, device_id, view)
        return view

    async def cancel_command(self, user_id: str, command_id: uuid.UUID) -> dict:
        async with self._session() as session:
            command = await self._require_command(session, user_id, command_id)
            if command.status in {"succeeded", "failed", "cancelled"}:
                raise InvalidCommand(f"command is already {command.status}")
            command.status = "cancelled"
            command.completed_at = self._now()
            return self._command_view(command)

    async def get_command(self, user_id: str, command_id: uuid.UUID) -> dict:
        async with self._session() as session:
            command = await self._require_command(session, user_id, command_id)
            return self._command_view(command)

    async def list_commands(
        self, user_id: str, *, status: str = "", limit: int = 20
    ) -> list[dict]:
        async with self._session() as session:
            await self.expire_commands_in_session(session, user_id=user_id)
            conditions = [PhoneCommand.user_id == user_id]
            if status:
                conditions.append(PhoneCommand.status == status)
            rows = await session.scalars(
                select(PhoneCommand)
                .where(*conditions)
                .order_by(PhoneCommand.created_at.desc())
                .limit(limit)
            )
            return [self._command_view(row) for row in rows]

    async def pending_commands(
        self, user_id: str, device_id: uuid.UUID
    ) -> list[dict]:
        """Claim approved commands for a device: they become dispatched."""
        async with self._session() as session:
            device = await self._require_device(session, user_id, device_id)
            now = self._now()
            await self.expire_commands_in_session(session, user_id=user_id)
            rows = list(
                await session.scalars(
                    select(PhoneCommand)
                    .where(
                        PhoneCommand.user_id == user_id,
                        PhoneCommand.device_id == device.id,
                        PhoneCommand.status == "approved",
                    )
                    .order_by(PhoneCommand.created_at.asc())
                    .limit(self.config.max_commands_per_poll)
                )
            )
            for command in rows:
                if command.expires_at is not None and as_utc(command.expires_at) < now:
                    command.status = "expired"
                    continue
                command.status = "dispatched"
                command.dispatched_at = now
            device.last_seen_at = now
            return [
                self._command_view(command)
                for command in rows
                if command.status == "dispatched"
            ]

    async def complete_command(
        self,
        user_id: str,
        command_id: uuid.UUID,
        *,
        status: str,
        result: str = "",
        error: str = "",
        device_id: uuid.UUID | None = None,
    ) -> dict:
        if status not in {"succeeded", "failed"}:
            raise InvalidCommand("a command result must be succeeded or failed")
        async with self._session() as session:
            command = await self._require_command(session, user_id, command_id)
            if device_id is not None and command.device_id != device_id:
                raise CommandNotFound("command not found")
            command.status = status
            command.result = _truncate(result, self.config.max_text_chars)
            command.error = _truncate(error, self.config.max_text_chars)
            command.completed_at = self._now()
            logger.info(
                "phone command %s %s", command.kind, status
            )
            view = self._command_view(command)
            device_id = command.device_id
        await self._emit("completed", user_id, device_id, view)
        return view

    async def _emit(
        self, event: str, user_id: str, device_id: uuid.UUID, command: dict
    ) -> None:
        if self.events is None:
            return
        try:
            if event == "approved":
                await self.events.command_approved(user_id, device_id, command)
            else:
                await self.events.command_completed(user_id, device_id, command)
        except Exception:  # noqa: BLE001 - delivery must not fail the command
            logger.warning("phone command event %s failed", event, exc_info=True)

    async def _require_command(
        self, session: AsyncSession, user_id: str, command_id: uuid.UUID
    ) -> PhoneCommand:
        command = await session.scalar(
            select(PhoneCommand).where(
                PhoneCommand.id == command_id,
                PhoneCommand.user_id == user_id,
            )
        )
        if command is None:
            raise CommandNotFound("command not found")
        return command

    async def expire_commands(self, user_id: str) -> int:
        async with self._session() as session:
            return await self.expire_commands_in_session(session, user_id=user_id)

    async def expire_commands_in_session(
        self, session: AsyncSession, *, user_id: str
    ) -> int:
        now = self._now()
        rows = list(
            await session.scalars(
                select(PhoneCommand).where(
                    PhoneCommand.user_id == user_id,
                    PhoneCommand.status.in_(("proposed", "approved")),
                    PhoneCommand.expires_at.is_not(None),
                    PhoneCommand.expires_at < now,
                )
            )
        )
        for command in rows:
            command.status = "expired"
        return len(rows)

    def _command_view(self, command: PhoneCommand) -> dict:
        return {
            "id": command.id,
            "device_id": command.device_id,
            "kind": command.kind,
            "payload": dict(command.payload or {}),
            "summary": command.summary,
            "status": command.status,
            "result": command.result,
            "error": command.error,
            "expires_at": aware_utc(command.expires_at),
            "approved_at": aware_utc(command.approved_at),
            "completed_at": aware_utc(command.completed_at),
            "created_at": aware_utc(command.created_at),
        }

    def _command_summary(self, kind: str, payload: dict[str, Any]) -> str:
        if kind == "calendar.create":
            window = _format_window(payload.get("starts_at"), payload.get("ends_at"))
            where = f" @ {payload['location']}" if payload.get("location") else ""
            return f"create calendar event \"{payload.get('title', '')}\" {window}{where}"
        if kind == "calendar.update":
            return f"update calendar event {payload.get('event_id', '')}"
        if kind == "calendar.delete":
            return f"delete calendar event {payload.get('event_id', '')}"
        if kind == "sms.send":
            body = _truncate(str(payload.get("body", "")), 120)
            return f"send SMS to {payload.get('address', '')}: {body}"
        return kind

    # -- retention and deletion ------------------------------------------

    async def purge(self, user_id: str | None = None) -> int:
        async with self._session() as session:
            return await self.purge_in_session(session, user_id=user_id)

    async def purge_in_session(
        self,
        session: AsyncSession,
        *,
        user_id: str | None,
        device: PhoneDevice | None = None,
    ) -> int:
        now = self._now()
        removed = 0
        if device is not None:
            logger.debug("retention check for device %s", device.id)
        specs = (
            (PhoneCalendarEvent, PhoneCalendarEvent.starts_at, "calendar"),
            (PhoneCall, PhoneCall.started_at, "call"),
            (PhoneMessage, PhoneMessage.received_at, "message"),
            (PhoneContact, None, "contact"),
            (PhoneSignal, PhoneSignal.occurred_at, "notification"),
            (PhoneSignal, PhoneSignal.occurred_at, "location"),
            (PhoneSignal, None, "app"),
        )
        for model, column, kind in specs:
            days = self.config.retention_days(kind)
            conditions = []
            if user_id is not None:
                conditions.append(model.user_id == user_id)
            if kind in SIGNAL_KINDS:
                conditions.append(PhoneSignal.kind == kind)
            if days > 0 and column is not None:
                cutoff = now - timedelta(days=days)
                conditions.append(column < cutoff)
            elif days > 0:
                # Snapshot kinds have no natural age; remove only tombstones
                # that are older than the window.
                conditions.append(model.deleted_at.is_not(None))
                conditions.append(model.deleted_at < now - timedelta(days=days))
            else:
                # 0 keeps everything, including tombstones.
                continue
            result = await session.execute(delete(model).where(*conditions))
            removed += int(result.rowcount or 0)
        # Commands are small and bounded; drop finished ones after 90 days.
        command_conditions = [
            PhoneCommand.status.in_(("succeeded", "failed", "cancelled", "expired")),
            PhoneCommand.created_at < now - timedelta(days=90),
        ]
        if user_id is not None:
            command_conditions.append(PhoneCommand.user_id == user_id)
        result = await session.execute(
            delete(PhoneCommand).where(*command_conditions)
        )
        removed += int(result.rowcount or 0)
        if removed:
            logger.info("phone retention removed %d rows", removed)
        return removed

    async def wipe_device(self, user_id: str, device_id: uuid.UUID) -> int:
        """Hard-delete every record this device sent, keeping its registration.

        The client resets its cursors after a successful wipe; otherwise it
        would resume from a position the server no longer has.
        """
        async with self._session() as session:
            device = await self._require_device(session, user_id, device_id)
            removed = 0
            for model in (
                PhoneCalendarEvent,
                PhoneCall,
                PhoneMessage,
                PhoneContact,
                PhoneSignal,
                PhoneCommand,
                PhoneSyncState,
            ):
                result = await session.execute(
                    delete(model).where(model.device_id == device.id)
                )
                removed += int(result.rowcount or 0)
            device.last_sync_at = None
            logger.info(
                "phone data wiped user=%s device=%s rows=%d",
                user_id,
                device.id,
                removed,
            )
            return removed

    async def delete_user_data(self, user_id: str) -> int:
        """Remove every phone record for an account, keeping device rows."""
        async with self._session() as session:
            removed = 0
            for model in (
                PhoneCalendarEvent,
                PhoneCall,
                PhoneMessage,
                PhoneContact,
                PhoneSignal,
                PhoneCommand,
                PhoneSyncState,
            ):
                result = await session.execute(
                    delete(model).where(model.user_id == user_id)
                )
                removed += int(result.rowcount or 0)
            return removed


def _signal_values(
    kind: str, item: Any, max_chars: int
) -> dict[str, Any]:
    """Translate a validated item into PhoneSignal columns."""
    if kind == "notification":
        posted = as_utc(item.posted_at)
        text = _truncate(item.text, max_chars)
        title = _truncate(item.title, max_chars)
        return {
            "external_id": item.external_id,
            "occurred_at": posted,
            "label": _truncate(item.app_label, 512),
            "body": _truncate("\n".join(part for part in (title, text) if part), max_chars),
            "payload": {
                "package": item.package_name,
                "title": title,
                "text": text,
                "category": item.category,
                "ongoing": item.is_ongoing,
                "clearable": item.is_clearable,
            },
        }
    if kind == "location":
        return {
            "external_id": item.external_id,
            "occurred_at": as_utc(item.captured_at),
            "label": item.provider or "location",
            "body": f"{item.latitude:.6f},{item.longitude:.6f}",
            "payload": {
                "latitude": item.latitude,
                "longitude": item.longitude,
                "accuracy_m": item.accuracy_m,
                "altitude_m": item.altitude_m,
                "speed_mps": item.speed_mps,
            },
        }
    if kind == "app":
        installed = as_utc(item.installed_at)
        updated = as_utc(item.updated_at)
        return {
            "external_id": item.external_id,
            "occurred_at": updated or installed,
            "label": _truncate(item.label, 512),
            "body": _truncate(item.package_name, max_chars),
            "payload": {
                "package": item.package_name,
                "version_name": item.version_name,
                "version_code": item.version_code,
                "is_system": item.is_system,
                "is_enabled": item.is_enabled,
                "installed_at": installed.isoformat() if installed else "",
                "updated_at": updated.isoformat() if updated else "",
            },
        }
    raise PhoneError(f"unsupported signal kind: {kind}")


def _signal_app_condition(app: str):
    """Match a notification's app label; the package name is in the payload."""
    if not app:
        return None
    pattern = _like(app)
    return or_(
        PhoneSignal.label.ilike(pattern, escape="\\"),
        PhoneSignal.body.ilike(pattern, escape="\\"),
    )


def _hit(kind: str, at, title: str, text: str, row: dict) -> dict:
    return {
        "kind": kind,
        "at": at,
        "title": _truncate(title, 200),
        "text": _truncate(text, 2000),
        "device_id": row.get("device_id", ""),
        "device_name": row.get("device_name", "phone"),
        "record_id": row.get("id", ""),
    }


def _join_text(*values: str) -> str:
    return " ".join(value for value in values if value)


def _merged_info(current: dict | None, incoming: dict | None) -> dict:
    merged = dict(current or {})
    merged.update(incoming or {})
    return merged


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_window(starts: Any, ends: Any) -> str:
    start = _parse_iso(starts)
    end = _parse_iso(ends)
    if start is None:
        return "(no start time)"
    if end is None:
        return start.strftime("%Y-%m-%d %H:%M")
    return f"{start.strftime('%Y-%m-%d %H:%M')}-{end.strftime('%H:%M')}"
