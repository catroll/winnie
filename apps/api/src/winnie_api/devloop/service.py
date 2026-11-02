"""The incident store: record, deduplicate, triage, diagnose, prune.

One method exists for every state change the console can cause, so the router
stays a thin translation layer and the tests can drive the store directly. The
service never reads ``app.state`` and never calls a model itself: the host
injects the session factory and, for diagnosis, the chat function.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import diagnose as diagnose_prompt
from . import evidence as evidence_bundle
from .events import ErrorEvent, coerce_status, utcnow
from .models import ErrorIncident

logger = logging.getLogger(__name__)

ChatFn = Callable[[list[dict[str, str]]], Awaitable[str]]


class IncidentNotFound(LookupError):
    """The incident id does not exist (or was pruned)."""


class ErrorService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        retention_days: int = 30,
    ) -> None:
        self.session_factory = session_factory
        self.retention_days = retention_days

    # -- writes ---------------------------------------------------------

    async def record(self, event: ErrorEvent) -> ErrorIncident | None:
        """Insert a new incident or fold the event into the existing one.

        Concurrent inserts of the same fingerprint are expected — the middleware
        and the log handler can both see one failure — so the unique constraint
        is the arbitrating write, and the loser retries as an update.
        """
        fingerprint = event.fingerprint()
        when = event.occurred_at or utcnow()
        message = (event.message or "")[:1000]

        async with self.session_factory() as session:
            existing = await session.scalar(
                select(ErrorIncident).where(ErrorIncident.fingerprint == fingerprint)
            )
            if existing is None:
                incident = ErrorIncident(
                    fingerprint=fingerprint,
                    source=event.source,
                    kind=event.kind,
                    service=event.service,
                    level=event.level,
                    message=message,
                    exception_type=event.exception_type[:200],
                    location=event.location[:400],
                    traceback=(event.traceback or "")[:20000],
                    context=event.context or {},
                    occurrences=1,
                    first_seen=when,
                    last_seen=when,
                    # A recovery is history, not work: it closes immediately so
                    # it never sits in the owner's open list next to the outage
                    # it explains.
                    status="resolved" if event.kind == "service_recovered" else "open",
                    resolved_at=when if event.kind == "service_recovered" else None,
                )
                session.add(incident)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    return await self._fold(fingerprint, event, when)
                await session.refresh(incident)
                return incident
            return await self._fold(fingerprint, event, when, session=session)

    async def _fold(
        self,
        fingerprint: str,
        event: ErrorEvent,
        when: datetime,
        *,
        session: AsyncSession | None = None,
    ) -> ErrorIncident | None:
        """Apply one repeat to the row that already owns this fingerprint."""
        async def apply(active: AsyncSession) -> ErrorIncident | None:
            incident = await active.scalar(
                select(ErrorIncident).where(ErrorIncident.fingerprint == fingerprint)
            )
            if incident is None:
                return None
            incident.occurrences += 1
            incident.last_seen = max(incident.last_seen, when)
            incident.message = (event.message or incident.message)[:1000]
            incident.context = event.context or incident.context
            if event.traceback:
                incident.traceback = event.traceback[:20000]
            # A resolved incident that fires again is open again; an ignored or
            # acknowledged one keeps the owner's decision.
            if incident.status == "resolved":
                incident.status = "open"
                incident.resolved_at = None
            await active.commit()
            await active.refresh(incident)
            return incident

        if session is not None:
            return await apply(session)
        async with self.session_factory() as fresh:
            return await apply(fresh)

    async def record_many(self, events: list[ErrorEvent]) -> int:
        recorded = 0
        for event in events:
            try:
                if await self.record(event) is not None:
                    recorded += 1
            except Exception:  # noqa: BLE001 - one bad spool line must not stop the rest
                logger.exception("failed to record an error event")
        return recorded

    async def set_status(self, incident_id: UUID, status: str) -> ErrorIncident:
        target = coerce_status(status)
        async with self.session_factory() as session:
            incident = await session.get(ErrorIncident, incident_id)
            if incident is None:
                raise IncidentNotFound(str(incident_id))
            now = utcnow()
            incident.status = target
            incident.acknowledged_at = now if target == "acknowledged" else None
            incident.ignored_at = now if target == "ignored" else None
            incident.resolved_at = now if target == "resolved" else None
            await session.commit()
            await session.refresh(incident)
            return incident

    async def auto_resolve(self, *, service: str, kinds: tuple[str, ...]) -> int:
        """Close open service incidents after a probe proves the service is up."""
        async with self.session_factory() as session:
            result = await session.execute(
                update(ErrorIncident)
                .where(
                    ErrorIncident.service == service,
                    ErrorIncident.kind.in_(kinds),
                    ErrorIncident.status == "open",
                )
                .values(status="resolved", resolved_at=utcnow())
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def purge(self, *, now: datetime | None = None) -> int:
        cutoff = (now or utcnow()) - timedelta(days=self.retention_days)
        async with self.session_factory() as session:
            result = await session.execute(
                delete(ErrorIncident).where(
                    ErrorIncident.status.in_(("resolved", "ignored")),
                    ErrorIncident.last_seen < cutoff,
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    # -- reads ----------------------------------------------------------

    async def get(self, incident_id: UUID) -> ErrorIncident:
        async with self.session_factory() as session:
            incident = await session.get(ErrorIncident, incident_id)
            if incident is None:
                raise IncidentNotFound(str(incident_id))
            return incident

    async def list_incidents(
        self,
        *,
        status: str = "open",
        service: str = "",
        source: str = "",
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ErrorIncident], int]:
        clauses = []
        if status and status != "all":
            clauses.append(ErrorIncident.status == status)
        if service:
            clauses.append(ErrorIncident.service == service)
        if source:
            clauses.append(ErrorIncident.source == source)
        if query:
            clauses.append(ErrorIncident.message.ilike(f"%{query}%"))
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        async with self.session_factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(ErrorIncident).where(*clauses)
            )
            rows = list(
                await session.scalars(
                    select(ErrorIncident)
                    .where(*clauses)
                    .order_by(ErrorIncident.last_seen.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
        return rows, int(total or 0)

    async def summary(self) -> dict[str, Any]:
        day_ago = utcnow() - timedelta(hours=24)
        async with self.session_factory() as session:
            by_status = {
                str(status): int(count)
                for status, count in (
                    await session.execute(
                        select(ErrorIncident.status, func.count()).group_by(
                            ErrorIncident.status
                        )
                    )
                ).all()
            }
            by_service = {
                str(service): int(count)
                for service, count in (
                    await session.execute(
                        select(ErrorIncident.service, func.count())
                        .where(ErrorIncident.status == "open")
                        .group_by(ErrorIncident.service)
                    )
                ).all()
            }
            last_day = await session.scalar(
                select(func.count())
                .select_from(ErrorIncident)
                .where(ErrorIncident.last_seen >= day_ago)
            )
            total = await session.scalar(
                select(func.count()).select_from(ErrorIncident)
            )
        return {
            "open": by_status.get("open", 0),
            "total": int(total or 0),
            "last_24h": int(last_day or 0),
            "by_status": by_status,
            "by_service": by_service,
        }

    # -- diagnosis ------------------------------------------------------

    async def diagnose(
        self,
        incident_id: UUID,
        *,
        chat: ChatFn,
        model_name: str,
        sources: evidence_bundle.EvidenceSources,
        max_evidence_bytes: int = 24000,
        timeout_seconds: float = 45.0,
    ) -> ErrorIncident:
        """Ask the bound model to explain one incident from bounded evidence."""
        incident = await self.get(incident_id)
        bundle = await evidence_bundle.build(
            incident, sources, max_bytes=max_evidence_bytes
        )
        messages = diagnose_prompt.build_messages(bundle)
        try:
            raw = await asyncio.wait_for(chat(messages), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"the diagnosis model did not answer within {timeout_seconds:g}s"
            ) from exc
        text = (raw or "").strip()
        answer = diagnose_prompt.parse_answer(text)
        payload = {
            "answer": answer,
            "raw": text[:8000],
            "model": model_name,
            "at": utcnow().isoformat(),
            "evidence_bytes": len(str(bundle)),
        }
        async with self.session_factory() as session:
            row = await session.get(ErrorIncident, incident_id)
            if row is None:
                raise IncidentNotFound(str(incident_id))
            row.diagnosis = payload
            row.diagnosis_at = utcnow()
            return await session_commit_and_refresh(session, row)


async def session_commit_and_refresh(
    session: AsyncSession, row: ErrorIncident
) -> ErrorIncident:
    await session.commit()
    await session.refresh(row)
    return row


def serialize(incident: ErrorIncident) -> dict[str, Any]:
    """The wire shape the console reads; timestamps are ISO-8601 without zone."""
    return {
        "id": str(incident.id),
        "fingerprint": incident.fingerprint,
        "source": incident.source,
        "kind": incident.kind,
        "service": incident.service,
        "level": incident.level,
        "message": incident.message,
        "exception_type": incident.exception_type,
        "location": incident.location,
        "traceback": incident.traceback,
        "context": incident.context or {},
        "occurrences": incident.occurrences,
        "first_seen": incident.first_seen.isoformat(),
        "last_seen": incident.last_seen.isoformat(),
        "status": incident.status,
        "acknowledged_at": _iso(incident.acknowledged_at),
        "ignored_at": _iso(incident.ignored_at),
        "resolved_at": _iso(incident.resolved_at),
        "diagnosis": incident.diagnosis,
        "diagnosis_at": _iso(incident.diagnosis_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
