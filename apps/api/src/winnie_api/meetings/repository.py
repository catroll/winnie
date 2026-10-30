"""Database access for the meetings context."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from winnie_api.integrations.summarize.base import MinutesDraft
from winnie_api.integrations.types import Usage
from winnie_api.meetings.models import (
    Job,
    Meeting,
    Minutes,
    Segment,
    UsageRecord,
    utcnow,
)


class MeetingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_meeting(
        self,
        *,
        user_id: str,
        title: str,
        local_language: str,
    ) -> Meeting:
        meeting = Meeting(
            user_id=user_id,
            title=title,
            local_language=local_language,
        )
        self.session.add(meeting)
        await self.session.commit()
        await self.session.refresh(meeting)
        return meeting

    async def get_meeting(
        self,
        meeting_id: uuid.UUID,
        *,
        user_id: str | None = None,
    ) -> Meeting | None:
        statement = select(Meeting).where(Meeting.id == meeting_id)
        if user_id is not None:
            statement = statement.where(Meeting.user_id == user_id)
        return await self.session.scalar(statement)

    async def list_meetings(
        self,
        *,
        user_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[Meeting], int]:
        condition = Meeting.user_id == user_id
        total = await self.session.scalar(
            select(func.count()).select_from(Meeting).where(condition)
        )
        rows = await self.session.scalars(
            select(Meeting)
            .where(condition)
            .order_by(Meeting.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total or 0)

    async def update_meeting(
        self,
        meeting_id: uuid.UUID,
        **fields: Any,
    ) -> Meeting | None:
        meeting = await self.session.get(Meeting, meeting_id)
        if meeting is None:
            return None
        for key, value in fields.items():
            if not hasattr(meeting, key):
                raise ValueError(f"unknown meeting field: {key}")
            setattr(meeting, key, value)
        await self.session.commit()
        return meeting

    async def replace_segments(
        self,
        meeting_id: uuid.UUID,
        segments: list[Segment],
    ) -> None:
        await self.session.execute(
            delete(Segment).where(Segment.meeting_id == meeting_id)
        )
        self.session.add_all(segments)
        await self.session.commit()

    async def get_segments(self, meeting_id: uuid.UUID) -> list[Segment]:
        rows = await self.session.scalars(
            select(Segment)
            .where(Segment.meeting_id == meeting_id)
            .order_by(Segment.seq)
        )
        return list(rows)

    async def apply_translations(
        self,
        meeting_id: uuid.UUID,
        translations: dict[int, str],
    ) -> None:
        for seq, translated_text in translations.items():
            segment = await self.session.scalar(
                select(Segment).where(
                    Segment.meeting_id == meeting_id,
                    Segment.seq == seq,
                )
            )
            if segment is None:
                continue
            segment.translated_text = translated_text
            segment.translation_status = "done"
        await self.session.commit()

    async def upsert_minutes(
        self,
        meeting_id: uuid.UUID,
        draft: MinutesDraft,
        *,
        model: str,
        prompt_version: str,
    ) -> Minutes:
        minutes = await self.session.get(Minutes, meeting_id)
        if minutes is None:
            minutes = Minutes(meeting_id=meeting_id)
            self.session.add(minutes)
        minutes.summary = draft.summary
        minutes.topics = [asdict(topic) for topic in draft.topics]
        minutes.decisions = [asdict(decision) for decision in draft.decisions]
        minutes.action_items = [
            asdict(action_item) for action_item in draft.action_items
        ]
        minutes.highlights = [asdict(highlight) for highlight in draft.highlights]
        minutes.model = model
        minutes.prompt_version = prompt_version
        await self.session.commit()
        await self.session.refresh(minutes)
        return minutes

    async def get_minutes(self, meeting_id: uuid.UUID) -> Minutes | None:
        return await self.session.get(Minutes, meeting_id)

    async def upsert_job(self, meeting_id: uuid.UUID, kind: str) -> Job:
        job = await self.session.scalar(
            select(Job).where(Job.meeting_id == meeting_id, Job.kind == kind)
        )
        if job is None:
            job = Job(meeting_id=meeting_id, kind=kind, status="queued")
            self.session.add(job)
            await self.session.commit()
            await self.session.refresh(job)
        return job

    async def update_job(
        self,
        job_id: uuid.UUID,
        status: str,
        *,
        error: str | None = None,
    ) -> Job | None:
        job = await self.session.get(Job, job_id)
        if job is None:
            return None
        job.status = status
        if status == "queued":
            job.finished_at = None
            job.last_error = None
        if status == "running":
            job.attempts += 1
            job.started_at = utcnow()
        if status in ("done", "failed"):
            job.finished_at = utcnow()
        if error is not None:
            job.last_error = error
        await self.session.commit()
        return job

    async def add_usage(self, meeting_id: uuid.UUID, usage: Usage) -> None:
        self.session.add(
            UsageRecord(
                meeting_id=meeting_id,
                provider=usage.provider,
                unit=usage.unit,
                quantity=usage.quantity,
                cost_micros=usage.cost_micros,
            )
        )
        await self.session.commit()

    async def delete_meeting(self, meeting_id: uuid.UUID) -> None:
        for model in (Segment, Minutes, Job, UsageRecord):
            await self.session.execute(
                delete(model).where(model.meeting_id == meeting_id)
            )
        await self.session.execute(delete(Meeting).where(Meeting.id == meeting_id))
        await self.session.commit()
