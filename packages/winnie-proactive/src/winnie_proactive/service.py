"""The engine: plan, dispatch, escalate, digest.

Three loops, not one. The **planner** turns findings into dated items and is
idempotent by (subject, occurrence, stage). The **dispatcher** sends what is
due after policy. The **escalator** advances an unacknowledged item along the
channel ladder. A fourth pass folds what is left into the digest, so nothing is
silently dropped.

The model is not here: detection is code, delivery is code, and a deployment
with no provider still gets every reminder.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_proactive import compose, detect, policy
from winnie_proactive.config import ProactiveConfig
from winnie_proactive.models import ProactiveItem, as_utc, utcnow
from winnie_proactive.ports import (
    Channel,
    ChannelError,
    Composer,
    Message,
)

logger = logging.getLogger(__name__)

BIRTHDAY_STAGES = ("heads_up", "prepare", "confirm", "day")
class ProactiveError(RuntimeError):
    """An operation that cannot be completed for a reportable reason."""


class ProactiveService:
    """Owns the item store, the policy and the delivery ladder."""

    def __init__(
        self,
        config: ProactiveConfig,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        channels: dict[str, Channel],
        resolve_owner: Callable[[], Awaitable[str]],
        root: Path,
        calendar: Any = None,
        contacts: Any = None,
        schedule: Any = None,
        composer: Composer | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.session_factory = session_factory
        self.channels = channels
        self.resolve_owner = resolve_owner
        self.root = Path(root).expanduser()
        self.calendar = calendar
        self.contacts = contacts
        self.schedule = schedule
        self.composer = composer
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.timezone = ZoneInfo(config.timezone)
        self._task: asyncio.Task[None] | None = None
        self._last_scan: datetime | None = None
        self._last_error = ""

    # -- lifecycle -------------------------------------------------------

    async def startup(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.config.enabled:
            return
        self._task = asyncio.create_task(self._loop())

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                now = self._now()
                if (
                    self._last_scan is None
                    or (now - self._last_scan).total_seconds()
                    >= self.config.scan_interval_seconds
                ):
                    await self.scan(now)
                    self._last_scan = now
                await self.digest(now)
                await self.dispatch(now)
                await self.escalate(now)
                self._last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad tick must not stop the loop
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("proactive tick failed")
            await asyncio.sleep(self.config.tick_seconds)

    # -- planning --------------------------------------------------------

    async def scan(self, now: datetime | None = None) -> int:
        """Create or refresh the items the next ``horizon_days`` calls for."""
        now_utc = self._naive(now or self._now())
        local_now = self._local(now_utc)
        owner = await self.resolve_owner()
        if not owner:
            return 0
        today = local_now.date()
        stages: list[dict[str, Any]] = []
        if self.calendar is not None and self.contacts is not None:
            stages.extend(
                await self._birthday_stages(today, local_now, now_utc)
            )
        if self.schedule is not None:
            stages.extend(await self._work_stages(today, local_now, now_utc))
        created = 0
        async with self.session_factory() as session:
            for stage in stages:
                if await self._upsert(session, owner, stage):
                    created += 1
            await session.commit()
        await self._expire(now_utc)
        return created

    async def _birthday_stages(
        self,
        today: date,
        local_now: datetime,
        now_utc: datetime,
    ) -> list[dict[str, Any]]:
        findings = await detect.birthday_findings(
            self.calendar, self.contacts, today=today, zone=self.timezone
        )
        overrides = self._subjects()
        # Apply the override after grouping, so a preferred source removes its
        # siblings rather than being filtered against a name it does not own.
        by_name: dict[str, list[detect.Birthday]] = {}
        for item in findings:
            by_name.setdefault(item.name, []).append(item)
        selected: list[detect.Birthday] = []
        conflicts: list[tuple[str, list[detect.Birthday]]] = []
        for name, rows in by_name.items():
            override = overrides.get(f"person:{name}") or {}
            prefer = str((override or {}).get("prefer") or "").strip()
            if prefer:
                kept = [row for row in rows if _matches_prefer(row, prefer)]
                rows = kept or rows
            dates = sorted({row.on for row in rows})
            if len(dates) > 1:
                conflicts.append((name, rows))
            selected.extend(rows)
        stages: list[dict[str, Any]] = []
        for finding in selected:
            days_before = (finding.on - today).days
            notes = finding.note
            if finding.year:
                notes = f"，{finding.on.year - finding.year} 岁{notes}"
            for index, lead in enumerate(self.config.birthday.lead_days):
                stage = (
                    BIRTHDAY_STAGES[index]
                    if index < len(BIRTHDAY_STAGES)
                    else f"t{lead}"
                )
                planned = self._at(finding.on - timedelta(days=lead), self.config.birthday.notify_hour)
                due = self._at(finding.on, 0)
                if lead == 0:
                    title, body = compose.birthday_day(finding.name, finding.on, notes)
                elif lead == 1:
                    title, body = compose.birthday_heads_up(
                        finding.name, finding.on, 1, notes
                    )
                elif lead <= 3:
                    title, body = compose.birthday_confirm(
                        finding.name, finding.on, lead, notes
                    )
                else:
                    title, body = compose.birthday_prepare(
                        finding.name, finding.on, lead, notes
                    )
                stages.append(
                    {
                        "kind": "birthday",
                        "subject_key": f"birthday:{finding.name}",
                        "occurrence_key": finding.on.isoformat(),
                        "stage": stage,
                        "priority": 2 if lead > 7 else 1,
                        "title": title,
                        "body": body,
                        "evidence": [finding.evidence],
                        "due_at": due,
                        "planned_at": planned,
                        "days_before": days_before,
                    }
                )
            follow = finding.on + timedelta(days=self.config.birthday.follow_up_days)
            title, body = compose.birthday_follow_up(finding.name, finding.on)
            stages.append(
                {
                    "kind": "birthday",
                    "subject_key": f"birthday:{finding.name}",
                    "occurrence_key": finding.on.isoformat(),
                    "stage": "follow_up",
                    "priority": 3,
                    "title": title,
                    "body": body,
                    "evidence": [finding.evidence],
                    "due_at": self._at(follow, 0),
                    "planned_at": self._at(follow, self.config.birthday.notify_hour),
                    "days_before": (follow - today).days,
                }
            )
        for name, rows in conflicts:
            if str((overrides.get(f"person:{name}") or {}).get("prefer") or "").strip():
                continue
            dates = sorted({row.on for row in rows})
            notes = [next(r.note for r in rows if r.on == on) for on in dates]
            title, body = compose.birthday_conflict(name, dates, notes)
            occurrence = "conflict:" + "|".join(on.isoformat() for on in dates)
            stages.append(
                {
                    "kind": "conflict",
                    "subject_key": f"birthday:{name}",
                    "occurrence_key": occurrence,
                    "stage": "conflict",
                    "priority": 2,
                    "title": title,
                    "body": body,
                    "evidence": [row.evidence for row in rows],
                    "due_at": self._at(today, 0),
                    "planned_at": local_now,
                    "days_before": 0,
                }
            )
        # Collapse a backlog: after downtime only the most recent past stage of
        # one occurrence survives, and only inside the catch-up window.
        return _collapse(stages, now_utc, self.config.catch_up_hours)

    async def _work_stages(
        self,
        today: date,
        local_now: datetime,
        now_utc: datetime,
    ) -> list[dict[str, Any]]:
        leave, overdue = await detect.work_findings(
            self.schedule,
            today=today,
            leave_buffer_minutes=self.config.work.leave_buffer_minutes,
            overdue_days=self.config.work.overdue_days,
        )
        stages: list[dict[str, Any]] = []
        buffer = self.config.work.leave_buffer_minutes
        for event in leave:
            try:
                hour, minute = (int(part) for part in event.start.split(":")[:2])
            except ValueError:
                continue
            start = self._at(today, hour, minute)
            planned = start - timedelta(minutes=buffer)
            if planned <= local_now:
                continue
            title, body = compose.leave_by(
                event.text,
                today,
                event.start,
                int((start - local_now).total_seconds() // 60),
            )
            stages.append(
                {
                    "kind": "leave_by",
                    "subject_key": f"schedule:{event.id}",
                    "occurrence_key": today.isoformat(),
                    "stage": "leave_by",
                    "priority": 0,
                    "title": title,
                    "body": body,
                    "evidence": [{"source": "schedule", "id": event.id, "text": event.text}],
                    "due_at": start,
                    "planned_at": planned,
                    "days_before": 0,
                }
            )
        if overdue:
            planned = self._at(today, self.config.work.overdue_hour)
            title, body = compose.overdue([(item.on, item.text) for item in overdue])
            stages.append(
                {
                    "kind": "overdue",
                    "subject_key": f"overdue:{today.isoformat()}",
                    "occurrence_key": today.isoformat(),
                    "stage": "overdue",
                    "priority": 2,
                    "title": title,
                    "body": body,
                    "evidence": [
                        {"source": "schedule", "id": item.id, "text": item.text}
                        for item in overdue
                    ],
                    "due_at": self._at(today, 23, 59),
                    "planned_at": planned,
                    "days_before": 0,
                }
            )
        return _collapse(stages, now_utc, self.config.catch_up_hours)

    async def _upsert(
        self, session: AsyncSession, owner: str, stage: dict[str, Any]
    ) -> bool:
        existing = await session.scalar(
            select(ProactiveItem).where(
                ProactiveItem.user_id == owner,
                ProactiveItem.subject_key == stage["subject_key"],
                ProactiveItem.occurrence_key == stage["occurrence_key"],
                ProactiveItem.stage == stage["stage"],
            )
        )
        if existing is None:
            session.add(
                ProactiveItem(
                    user_id=owner,
                    kind=stage["kind"],
                    subject_key=stage["subject_key"],
                    occurrence_key=stage["occurrence_key"],
                    stage=stage["stage"],
                    priority=stage["priority"],
                    title=stage["title"],
                    body=stage["body"],
                    evidence=stage["evidence"],
                    due_at=as_utc(stage["due_at"]),
                    planned_at=as_utc(stage["planned_at"]),
                    ack_token=secrets.token_urlsafe(24),
                )
            )
            return True
        if existing.status != "planned":
            return False
        # The date or the wording may have changed since the first plan.
        existing.priority = stage["priority"]
        existing.title = stage["title"]
        existing.body = stage["body"]
        existing.evidence = stage["evidence"]
        existing.due_at = as_utc(stage["due_at"])
        existing.planned_at = as_utc(stage["planned_at"])
        return False

    # -- dispatch --------------------------------------------------------

    async def dispatch(self, now: datetime | None = None) -> int:
        now_utc = self._naive(now or self._now())
        local_now = self._local(now_utc)
        owner = await self.resolve_owner()
        if not owner:
            return 0
        sent_today = await self._sent_today(owner, local_now.date())
        sent = 0
        async with self.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(ProactiveItem)
                    .where(
                        ProactiveItem.user_id == owner,
                        ProactiveItem.status.in_(("planned", "snoozed")),
                        ProactiveItem.priority < 2,
                        ProactiveItem.planned_at <= now_utc,
                    )
                    .order_by(ProactiveItem.planned_at.asc())
                    .limit(50)
                )
            )
            for item in rows:
                if item.status == "snoozed":
                    item.status = "planned"
                planned = as_utc(item.planned_at)
                if planned is not None and now_utc - planned > timedelta(
                    hours=self.config.catch_up_hours
                ):
                    # A birthday reminder at 22:15 for a date that ended the
                    # night before is noise, not a reminder. The digest still
                    # carries what is upcoming.
                    item.status = "expired"
                    continue
                decision = policy.decide(
                    priority=item.priority,
                    due_at=item.due_at,
                    now_local=local_now,
                    timezone=self.timezone,
                    quiet_hours=self.config.quiet_hours,
                    allow_urgent=self.config.allow_urgent,
                    max_interrupts=self.config.max_interrupts_per_day,
                    sent_today=sent_today,
                    digest_morning=self.config.digest.morning,
                    digest_evening=self.config.digest.evening,
                )
                if decision.action == "defer" and decision.until is not None:
                    item.planned_at = as_utc(decision.until)
                    continue
                if decision.action == "digest":
                    # The next digest pass picks it up; keep it planned but out
                    # of the dispatcher's way until that slot.
                    if decision.until is not None:
                        item.planned_at = as_utc(decision.until)
                    continue
                if await self._deliver(session, item, local_now):
                    sent += 1
                    if item.priority <= 1:
                        sent_today += 1
            await session.commit()
        return sent

    async def escalate(self, now: datetime | None = None) -> int:
        now_utc = self._naive(now or self._now())
        local_now = self._local(now_utc)
        owner = await self.resolve_owner()
        if not owner:
            return 0
        sent = 0
        async with self.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(ProactiveItem).where(
                        ProactiveItem.user_id == owner,
                        ProactiveItem.status == "sent",
                        ProactiveItem.priority <= 1,
                    )
                )
            )
            for item in rows:
                attempts = _successful(item.channels)
                step = len(attempts) - 1
                if step < 0 or step >= len(self.config.escalation.after_minutes):
                    continue
                last = as_utc(item.last_sent_at) or as_utc(item.planned_at)
                if last is None:
                    continue
                deadline = last + timedelta(
                    minutes=self.config.escalation.after_minutes[step]
                )
                if now_utc < deadline:
                    continue
                if await self._deliver_next(session, item, local_now):
                    sent += 1
            await session.commit()
        return sent

    async def digest(self, now: datetime | None = None) -> int:
        """Send the morning/evening digest once per slot, if it has content."""
        now_utc = self._naive(now or self._now())
        local_now = self._local(now_utc)
        slot = self._current_slot(local_now)
        if slot is None:
            return 0
        slot_at, name = slot
        owner = await self.resolve_owner()
        if not owner:
            return 0
        subject = f"digest:{local_now.date().isoformat()}:{name}"
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(ProactiveItem).where(
                    ProactiveItem.user_id == owner,
                    ProactiveItem.subject_key == subject,
                )
            )
            if existing is not None:
                return 0
            planned = list(
                await session.scalars(
                    select(ProactiveItem)
                    .where(
                        ProactiveItem.user_id == owner,
                        ProactiveItem.status == "planned",
                        ProactiveItem.priority >= 2,
                        ProactiveItem.planned_at <= now_utc,
                    )
                    .order_by(ProactiveItem.planned_at.asc())
                    .limit(50)
                )
            )
            stale = list(
                await session.scalars(
                    select(ProactiveItem).where(
                        ProactiveItem.user_id == owner,
                        ProactiveItem.status == "sent",
                        ProactiveItem.priority <= 1,
                    )
                )
            )
            exhausted = [
                item for item in stale if self._escalation_exhausted(item)
            ]
            entries = [
                f"{_when(item)} {item.title}" for item in planned + exhausted
            ]
            if not entries:
                return 0
            title, body = compose.digest(entries, name)
            item = ProactiveItem(
                user_id=owner,
                kind="digest",
                subject_key=subject,
                occurrence_key=local_now.date().isoformat(),
                stage="digest",
                priority=2,
                title=title,
                body=body,
                evidence=[],
                due_at=as_utc(slot_at),
                planned_at=now_utc,
                ack_token=secrets.token_urlsafe(24),
            )
            session.add(item)
            await session.flush()
            if not await self._deliver(session, item, local_now, digest=True):
                # The row was committed before the send; remove it so the next
                # tick can try this slot again instead of finding a stale digest.
                await session.delete(item)
                await session.commit()
                return 0
            for row in planned:
                row.status = "digested"
            for row in exhausted:
                row.status = "digested"
            await session.commit()
        return 1

    # -- delivery --------------------------------------------------------

    async def _deliver(
        self,
        session: AsyncSession,
        item: ProactiveItem,
        local_now: datetime,
        *,
        digest: bool = False,
    ) -> bool:
        await self._maybe_compose(session, item)
        # A channel may write to the same database (push does). Holding this
        # session's write transaction open while it does turns SQLite into
        # "database is locked", so persist our own state first.
        await session.commit()
        message = self._message(item, digest=digest)
        for name in self._available_order():
            if _attempted(item.channels, name):
                continue
            channel = self.channels[name]
            try:
                receipt = await channel.send(message)
            except ChannelError as exc:
                item.channels = [
                    *list(item.channels or []),
                    {"name": name, "at": self._now().isoformat(), "error": str(exc)},
                ]
                continue
            item.channels = [
                *list(item.channels or []),
                {"name": name, "at": self._now().isoformat(), "receipt": receipt},
            ]
            item.status = "sent"
            item.sent_at = item.sent_at or self._naive(self._now())
            item.last_sent_at = self._naive(self._now())
            return True
        # Nothing could deliver. Keep it planned and retry later; the expiry
        # pass and the digest are the backstop.
        item.planned_at = self._naive(self._now()) + timedelta(minutes=30)
        return False

    async def _maybe_compose(
        self, session: AsyncSession, item: ProactiveItem
    ) -> None:
        """Let the model rewrite the body once, never the date.

        The template is what is stored at plan time; this only runs just before
        the first send and only for items with an owner-facing stage. A failure
        marks the attempt anyway, so a retry cannot hammer the model.
        """
        if self.composer is None or item.composed_at is not None or item.kind == "digest":
            return
        try:
            body = await self.composer.compose(
                kind=item.kind,
                title=item.title,
                body=item.body,
                due_at=item.due_at,
                evidence=list(item.evidence or []),
            )
        except Exception as exc:  # noqa: BLE001 - a reminder must survive the model
            logger.warning("composer failed for %s: %s", item.id, exc)
            body = ""
        item.composed_at = self._naive(self._now())
        if body.strip():
            item.body = body.strip()[:1200]
        await session.flush()

    async def _deliver_next(
        self,
        session: AsyncSession,
        item: ProactiveItem,
        local_now: datetime,
    ) -> bool:
        return await self._deliver(session, item, local_now)

    def _escalation_exhausted(self, item: ProactiveItem) -> bool:
        tried = {attempt.get("name") for attempt in item.channels or []}
        return not (set(self._available_order()) - tried)

    def _message(self, item: ProactiveItem, *, digest: bool = False) -> Message:
        return Message(
            title=item.title,
            body=item.body,
            data={
                "item_id": str(item.id),
                "kind": item.kind,
                "ack_token": item.ack_token,
                "ack_url": self._ack_url(item),
                "digest": digest,
            },
        )

    def _available_order(self) -> list[str]:
        return [name for name in self.config.channels.order if name in self.channels]

    # -- ack and control -------------------------------------------------

    async def test_message(self, *, channel: str = "") -> dict[str, Any]:
        """Deliver one real item so the owner can try the notification actions.

        A test that is not a real item cannot show the actions or prove the
        acknowledgement path, so this creates an item like any other and sends
        it now, bypassing quiet hours on purpose. An explicit channel targets
        one rung, which is how a newly configured ntfy or mailbox is checked.
        """
        owner = await self.resolve_owner()
        if not owner:
            raise ProactiveError("no owner is resolved")
        now = self._naive(self._now())
        local = self._local(now)
        item = ProactiveItem(
            user_id=owner,
            kind="test",
            subject_key=f"test:{now.isoformat(timespec='seconds')}",
            occurrence_key=local.date().isoformat(),
            stage="test",
            priority=1,
            title="测试提醒 · 现在",
            body="这是一条测试：按「收到」会停止升级，按「一小时后」会稍后提醒。",
            due_at=now,
            planned_at=now,
            ack_token=secrets.token_urlsafe(24),
        )
        async with self.session_factory() as session:
            session.add(item)
            await session.flush()
            await self._maybe_compose(session, item)
            if channel:
                name = channel.strip()
                if name not in self.channels:
                    raise ProactiveError(f"channel {name} is not configured")
                receipt = ""
                error = ""
                try:
                    receipt = await self.channels[name].send(self._message(item))
                except ChannelError as exc:
                    error = str(exc)
                item.channels = [
                    {
                        "name": name,
                        "at": self._now().isoformat(),
                        "receipt": receipt,
                        "error": error,
                    }
                ]
                if receipt:
                    item.status = "sent"
                    item.sent_at = item.last_sent_at = now
                await session.commit()
                return {
                    "delivered": bool(receipt),
                    "channel": name,
                    "receipt": receipt,
                    "error": error,
                    "item": _item_dict(item),
                }
            delivered = await self._deliver(session, item, local)
            await session.commit()
            return {
                "delivered": delivered,
                "channels": list(item.channels or []),
                "item": _item_dict(item),
            }

    async def items(
        self,
        user_id: str,
        *,
        status: str = "",
        kind: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            conditions = [ProactiveItem.user_id == user_id]
            if status:
                conditions.append(ProactiveItem.status == status)
            if kind:
                conditions.append(ProactiveItem.kind == kind)
            rows = list(
                await session.scalars(
                    select(ProactiveItem)
                    .where(*conditions)
                    .order_by(ProactiveItem.planned_at.desc())
                    .limit(limit)
                )
            )
            return [_item_dict(row) for row in rows]

    async def ack(
        self, user_id: str, item_id: str, *, via: str = "api"
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            row = await self._get(session, user_id, item_id)
            if row.status != "acked":
                row.status = "acked"
                row.acked_at = self._naive(self._now())
                row.acked_via = via
            await session.commit()
            return _item_dict(row)

    async def ack_by_token(self, token: str) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            row = await session.scalar(
                select(ProactiveItem).where(ProactiveItem.ack_token == token)
            )
            if row is None:
                return None
            if row.status != "acked":
                row.status = "acked"
                row.acked_at = self._naive(self._now())
                row.acked_via = "email"
            await session.commit()
            return _item_dict(row)

    async def snooze(
        self, user_id: str, item_id: str, *, minutes: int
    ) -> dict[str, Any]:
        minutes = max(5, min(7 * 24 * 60, minutes))
        async with self.session_factory() as session:
            row = await self._get(session, user_id, item_id)
            row.status = "snoozed"
            row.planned_at = self._naive(self._now()) + timedelta(minutes=minutes)
            await session.commit()
            return _item_dict(row)

    async def mute(self, user_id: str, item_id: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            row = await self._get(session, user_id, item_id)
            row.status = "muted"
            await session.commit()
            return _item_dict(row)

    def set_prefer(self, name: str, prefer: str) -> None:
        """Write the owner's choice of source for one person's birthday.

        A file, not a row: the owner can read it, edit it and keep it in the
        same place as the rest of their rules, and the next scan applies it.
        """
        path = self.root / "subjects.yaml"
        data: dict[str, Any] = {}
        if path.is_file():
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    data = raw
            except (OSError, yaml.YAMLError):
                data = {}
        subjects = data.get("subjects")
        if not isinstance(subjects, dict):
            subjects = {}
            data["subjects"] = subjects
        subjects[f"person:{name}"] = {"prefer": prefer}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        temporary.replace(path)

    async def _get(self, session: AsyncSession, user_id: str, item_id: str) -> ProactiveItem:
        row = await session.scalar(
            select(ProactiveItem).where(ProactiveItem.id == _uuid(item_id))
        )
        if row is None or row.user_id != user_id:
            raise ProactiveError("item not found")
        return row

    async def status(self) -> dict[str, Any]:
        owner = await self.resolve_owner()
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(ProactiveItem.status, func.count())
                    .where(ProactiveItem.user_id == owner)
                    .group_by(ProactiveItem.status)
                )
            ).all()
            newest = await session.scalar(
                select(ProactiveItem.updated_at)
                .where(ProactiveItem.user_id == owner)
                .order_by(ProactiveItem.updated_at.desc())
                .limit(1)
            )
        return {
            "enabled": self.config.enabled,
            "owner": owner,
            "channels": self._available_order(),
            "counts": {status: count for status, count in rows},
            "last_scan_at": self._last_scan.isoformat() if self._last_scan else "",
            "last_error": self._last_error,
            "latest_update": _aware(newest).isoformat() if newest else "",
        }

    # -- helpers ---------------------------------------------------------

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _naive(self, moment: datetime) -> datetime:
        return as_utc(moment)

    def _local(self, utc: datetime) -> datetime:
        return utc.replace(tzinfo=timezone.utc).astimezone(self.timezone)

    def _at(self, day: date, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute), tzinfo=self.timezone)

    def _subjects(self) -> dict[str, dict[str, Any]]:
        path = self.root / "subjects.yaml"
        if not path.is_file():
            return {}
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("proactive subjects file is unreadable: %s", exc)
            return {}
        subjects = raw.get("subjects") if isinstance(raw, dict) else None
        if not isinstance(subjects, dict):
            return {}
        return {str(key): value for key, value in subjects.items() if isinstance(value, dict)}

    def _ack_url(self, item: ProactiveItem) -> str:
        base = self.config.public_url.rstrip("/")
        if not base or not item.ack_token:
            return ""
        return f"{base}/api/v1/proactive/ack?token={item.ack_token}"

    async def _sent_today(self, owner: str, day: date) -> int:
        start = self._at(day, 0)
        async with self.session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count()).where(
                        ProactiveItem.user_id == owner,
                        ProactiveItem.kind != "digest",
                        ProactiveItem.priority <= 1,
                        ProactiveItem.last_sent_at.is_not(None),
                        ProactiveItem.last_sent_at >= as_utc(start),
                    )
                )
                or 0
            )

    async def _expire(self, now_utc: datetime) -> None:
        limit = now_utc - timedelta(days=self.config.expiry_days)
        async with self.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(ProactiveItem).where(
                        ProactiveItem.status.in_(
                            ("planned", "sent", "snoozed")
                        ),
                        ProactiveItem.due_at.is_not(None),
                        ProactiveItem.due_at < limit,
                    )
                )
            )
            for row in rows:
                row.status = "expired"
            await session.commit()

    def _current_slot(self, local_now: datetime) -> tuple[datetime, str] | None:
        """The digest slot this tick belongs to, or None outside its window."""
        slots = [
            (self._at(local_now.date(), *_hhmm(self.config.digest.morning)), "morning"),
            (self._at(local_now.date(), *_hhmm(self.config.digest.evening)), "evening"),
        ]
        chosen: tuple[datetime, str] | None = None
        for moment, name in slots:
            if local_now >= moment and (local_now - moment).total_seconds() <= 2 * 3600:
                chosen = (moment, name)
        return chosen


def _hhmm(value: str) -> tuple[int, int]:
    hour, _, minute = value.partition(":")
    return int(hour), int(minute or 0)


def _collapse(
    stages: list[dict[str, Any]], now_utc: datetime, catch_up_hours: int
) -> list[dict[str, Any]]:
    """Keep future stages plus at most the newest past stage per occurrence."""
    horizon = now_utc - timedelta(hours=catch_up_hours)
    newest: dict[tuple[str, str], dict[str, Any]] = {}
    kept: list[dict[str, Any]] = []
    for stage in stages:
        planned = stage["planned_at"]
        planned = planned.replace(tzinfo=timezone.utc) if planned.tzinfo is None else planned
        planned_utc = planned.astimezone(timezone.utc).replace(tzinfo=None)
        if planned_utc > now_utc:
            kept.append(stage)
            continue
        if planned_utc < horizon:
            continue
        key = (stage["subject_key"], stage["occurrence_key"])
        current = newest.get(key)
        if current is None or planned_utc > current["_planned_utc"]:
            newest[key] = {**stage, "_planned_utc": planned_utc}
    kept.extend({k: v for k, v in stage.items() if k != "_planned_utc"} for stage in newest.values())
    return kept


def _successful(channels: list) -> list[dict]:
    return [entry for entry in channels or [] if entry.get("receipt")]


def _attempted(channels: list, name: str) -> bool:
    return any(entry.get("name") == name for entry in channels or [])


def _when(item: ProactiveItem) -> str:
    if item.due_at is None:
        return item.occurrence_key
    return item.due_at.date().isoformat()


def _uuid(value: str):
    import uuid

    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        raise ProactiveError("not an item id") from None


def _matches_prefer(finding: detect.Birthday, prefer: str) -> bool:
    if prefer == finding.on.isoformat():
        return True
    source = finding.evidence.get("source")
    if source == "calendar" and prefer.startswith("calendar:"):
        return finding.evidence.get("title") == prefer.split(":", 1)[1]
    if source == "contacts" and prefer == "contacts":
        return True
    return False


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc)


def _item_dict(row: ProactiveItem) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "subject_key": row.subject_key,
        "occurrence_key": row.occurrence_key,
        "stage": row.stage,
        "priority": row.priority,
        "title": row.title,
        "body": row.body,
        "actions": list(row.actions or []),
        "evidence": list(row.evidence or []),
        "due_at": _aware(row.due_at),
        "planned_at": _aware(row.planned_at),
        "status": row.status,
        "channels": list(row.channels or []),
        "acked_at": _aware(row.acked_at),
        "acked_via": row.acked_via,
        "feedback": row.feedback,
        "created_at": _aware(row.created_at),
    }


__all__ = ["ProactiveError", "ProactiveService"]
