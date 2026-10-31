"""Meeting use cases: upload, processing pipeline, reads and export."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_meetings.integrations.asr.base import ASRProvider
from winnie_meetings.integrations.asr.realtime import RealtimeASRProvider
from winnie_meetings.integrations.summarize.base import (
    MinutesDraft,
    Summarizer,
    TranscriptLine,
    TranscriptView,
)
from winnie_meetings.integrations.translate.base import Translator
from winnie_core.types import AudioRef, Usage
from winnie_core.jobs.queue import JobQueue
from winnie_knowledge.models import (
    MeetingNote,
    NoteActionItem,
    NoteContent,
    NoteDecision,
    NoteHighlight,
    NoteTopic,
)
from winnie_knowledge.note import MeetingNoteWriter
from winnie_meetings.language import detect_language
from winnie_meetings.models import Meeting, Minutes, Segment, utcnow
from winnie_meetings.repository import MeetingRepository
from winnie_core.storage import Storage
from winnie_meetings.config import MeetingsConfig

logger = logging.getLogger(__name__)


class MeetingNotFound(Exception):
    pass


class MeetingNotReady(Exception):
    pass


class MeetingQueueError(Exception):
    pass


class MeetingService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        config: MeetingsConfig,
        storage: Storage,
        asr: ASRProvider,
        realtime_asr: RealtimeASRProvider,
        translator: Translator,
        summarizer: Summarizer,
        note_writer: MeetingNoteWriter,
        queue: JobQueue | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.config = config
        self.storage = storage
        self.asr = asr
        self.realtime_asr = realtime_asr
        self.translator = translator
        self.summarizer = summarizer
        self.note_writer = note_writer
        self.queue = queue

    def set_queue(self, queue: JobQueue) -> None:
        self.queue = queue

    async def create_meeting(
        self,
        *,
        user_id: str,
        title: str,
        local_language: str | None = None,
        source: str = "file",
    ) -> Meeting:
        language = local_language or self.config.local_language
        async with self.session_factory() as session:
            repository = MeetingRepository(session)
            return await repository.create_meeting(
                user_id=user_id,
                title=title,
                local_language=language,
                source=source,
            )

    async def list_meetings(
        self,
        *,
        user_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[Meeting], int]:
        async with self.session_factory() as session:
            repository = MeetingRepository(session)
            return await repository.list_meetings(
                user_id=user_id,
                limit=limit,
                offset=offset,
            )

    async def get_meeting(
        self,
        meeting_id: uuid.UUID,
        *,
        user_id: str,
    ) -> Meeting:
        async with self.session_factory() as session:
            meeting = await MeetingRepository(session).get_meeting(
                meeting_id, user_id=user_id
            )
        if meeting is None:
            raise MeetingNotFound(str(meeting_id))
        return meeting

    async def upload_audio(
        self,
        meeting_id: uuid.UUID,
        *,
        user_id: str,
        filename: str,
        chunks: AsyncIterator[bytes],
    ) -> Meeting:
        meeting = await self.get_meeting(meeting_id, user_id=user_id)
        safe_name = Path(filename or "audio.bin").name
        key = f"{user_id}/{meeting_id}/{safe_name}"
        max_bytes = self.config.max_upload_mb * 1024 * 1024
        await self.storage.save(key, chunks, max_bytes=max_bytes)

        if meeting.audio_key and meeting.audio_key != key:
            await self.storage.delete(meeting.audio_key)

        async with self.session_factory() as session:
            updated = await MeetingRepository(session).update_meeting(
                meeting_id,
                audio_key=key,
                status="uploaded",
                stage="upload",
                error=None,
            )
        if updated is None:
            raise MeetingNotFound(str(meeting_id))
        return updated

    async def request_processing(
        self,
        meeting_id: uuid.UUID,
        *,
        user_id: str,
    ) -> Meeting:
        meeting = await self.get_meeting(meeting_id, user_id=user_id)
        if not meeting.audio_key:
            raise MeetingNotReady("upload audio before requesting transcription")

        async with self.session_factory() as session:
            repository = MeetingRepository(session)
            await repository.update_meeting(
                meeting_id,
                status="queued",
                stage="queued",
                error=None,
            )
            job = await repository.upsert_job(meeting_id, "process")
            job_id = job.id
            await repository.update_job(job_id, "queued")

        if self.queue is None:
            await self.process(meeting_id)
        else:
            try:
                await self.queue.enqueue(meeting_id)
            except Exception as exc:  # noqa: BLE001
                await self._mark_failed(meeting_id, job_id, exc)
                raise MeetingQueueError(
                    "could not enqueue meeting processing"
                ) from exc

        async with self.session_factory() as session:
            processed = await MeetingRepository(session).get_meeting(
                meeting_id, user_id=user_id
            )
        if processed is None:
            raise MeetingNotFound(str(meeting_id))
        return processed

    async def process(self, meeting_id: uuid.UUID) -> None:
        job_id: uuid.UUID | None = None
        try:
            meeting = await self._load_meeting(meeting_id)
            if meeting is None:
                return
            job_id = await self._mark_job_running(meeting_id)
            await self._process(meeting)
        except Exception as exc:  # noqa: BLE001
            logger.exception("meeting processing failed: %s", meeting_id)
            await self._mark_failed(meeting_id, job_id, exc)

    async def _process(self, meeting: Meeting) -> None:
        if not meeting.audio_key:
            raise MeetingNotReady("meeting has no audio")

        await self._update(meeting.id, status="transcribing", stage="asr")
        audio = AudioRef(
            key=meeting.audio_key,
            local_path=self.storage.path_for(meeting.audio_key),
        )
        asr_result = await self.asr.transcribe(
            audio, language_hint=meeting.local_language
        )
        if not asr_result.segments:
            raise RuntimeError("ASR returned no segments")
        duration_ms = max(
            (segment.end_ms for segment in asr_result.segments), default=0
        )

        segments: list[Segment] = []
        previous_language: str | None = None
        for seq, item in enumerate(asr_result.segments, start=1):
            language = item.language
            confidence = item.language_confidence
            if not language:
                guess = detect_language(item.text)
                if guess is not None:
                    language = guess.code
                    confidence = guess.confidence
                elif previous_language is not None:
                    # Short numbers or filler inherit their neighbour.
                    language = previous_language
                    confidence = 0.0
                else:
                    language = meeting.local_language
                    confidence = 0.0
            previous_language = language

            needs_translation = not _same_language(language, meeting.local_language)
            segments.append(
                Segment(
                    meeting_id=meeting.id,
                    seq=seq,
                    start_ms=item.start_ms,
                    end_ms=item.end_ms,
                    speaker=item.speaker,
                    text=item.text,
                    detected_language=language,
                    language_confidence=confidence,
                    translation_status="pending" if needs_translation else "skipped",
                )
            )
        await self._replace_segments(meeting.id, segments)
        await self._record_usage(meeting.id, asr_result.usage)
        await self._update(meeting.id, status="transcribed", stage="asr")

        pending = [s for s in segments if s.translation_status == "pending"]
        if pending:
            await self._update(meeting.id, status="translating", stage="translate")
            translation = await self.translator.translate(
                [s.text for s in pending],
                source=None,
                target=meeting.local_language,
            )
            if len(translation.texts) != len(pending):
                raise RuntimeError(
                    "translator returned a different number of texts than requested"
                )
            await self._apply_translations(
                meeting.id,
                {
                    segment.seq: translated
                    for segment, translated in zip(pending, translation.texts)
                },
            )
            await self._record_usage(meeting.id, translation.usage)

        await self._update(meeting.id, status="summarizing", stage="minutes")
        stored_segments = await self._get_segments(meeting.id)
        draft = await self.summarizer.summarize(
            TranscriptView(
                meeting_title=meeting.title,
                local_language=meeting.local_language,
                lines=[
                    TranscriptLine(
                        seq=segment.seq,
                        text=segment.text,
                        translated_text=segment.translated_text,
                        detected_language=segment.detected_language,
                    )
                    for segment in stored_segments
                ],
            )
        )
        await self._save_minutes(meeting.id, draft)
        await self._record_usage(meeting.id, draft.usage)
        await self.note_writer.write(_note(meeting), _note_content(draft))
        await self._update(
            meeting.id,
            status="ready",
            stage="done",
            audio_duration_ms=duration_ms or None,
        )

    async def regenerate_minutes(
        self,
        meeting_id: uuid.UUID,
        *,
        user_id: str,
    ) -> Minutes:
        meeting = await self.get_meeting(meeting_id, user_id=user_id)
        segments = await self._get_segments(meeting_id)
        if not segments:
            raise MeetingNotReady("meeting has no transcript")
        draft = await self.summarizer.summarize(
            TranscriptView(
                meeting_title=meeting.title,
                local_language=meeting.local_language,
                lines=[
                    TranscriptLine(
                        seq=segment.seq,
                        text=segment.text,
                        translated_text=segment.translated_text,
                        detected_language=segment.detected_language,
                    )
                    for segment in segments
                ],
            )
        )
        minutes = await self._save_minutes(meeting_id, draft)
        await self._record_usage(meeting_id, draft.usage)
        await self.note_writer.write(_note(meeting), _note_content(draft))
        return minutes

    async def get_transcript(
        self,
        meeting_id: uuid.UUID,
        *,
        user_id: str,
    ) -> tuple[Meeting, list[Segment]]:
        meeting = await self.get_meeting(meeting_id, user_id=user_id)
        return meeting, await self._get_segments(meeting_id)

    async def get_minutes(
        self,
        meeting_id: uuid.UUID,
        *,
        user_id: str,
    ) -> tuple[Meeting, Minutes]:
        meeting = await self.get_meeting(meeting_id, user_id=user_id)
        async with self.session_factory() as session:
            minutes = await MeetingRepository(session).get_minutes(meeting_id)
        if minutes is None:
            raise MeetingNotReady("minutes have not been generated yet")
        return meeting, minutes

    async def export(
        self,
        meeting_id: uuid.UUID,
        *,
        user_id: str,
        fmt: str,
    ) -> tuple[str, str, str]:
        meeting = await self.get_meeting(meeting_id, user_id=user_id)
        async with self.session_factory() as session:
            minutes = await MeetingRepository(session).get_minutes(meeting_id)

        if fmt == "txt":
            return (
                self._render_txt(meeting, minutes),
                "text/plain; charset=utf-8",
                f"{meeting.id}.txt",
            )
        return (
            self._render_markdown(meeting, minutes),
            "text/markdown; charset=utf-8",
            f"{meeting.id}.md",
        )

    async def purge_expired_audio(self, *, now: datetime | None = None) -> int:
        """Delete audio past the retention window while keeping the meeting.

        Transcripts and minutes outlive audio. A failed storage delete is logged
        and left for the next run instead of marking the meeting as purged.
        """
        days = self.config.audio_retention_days
        if days <= 0:
            return 0
        when = now or utcnow()
        cutoff = when - timedelta(days=days)

        async with self.session_factory() as session:
            meetings = await MeetingRepository(session).list_expired_audio(cutoff)

        purged = 0
        for meeting in meetings:
            if not meeting.audio_key:
                continue
            try:
                await self.storage.delete(meeting.audio_key)
            except Exception:  # noqa: BLE001
                logger.exception("failed to delete expired audio: %s", meeting.id)
                continue
            async with self.session_factory() as session:
                await MeetingRepository(session).update_meeting(
                    meeting.id,
                    audio_key=None,
                    audio_deleted_at=when,
                )
            purged += 1
        return purged

    async def delete_meeting(
        self,
        meeting_id: uuid.UUID,
        *,
        user_id: str,
    ) -> None:
        meeting = await self.get_meeting(meeting_id, user_id=user_id)
        if meeting.audio_key:
            await self.storage.delete(meeting.audio_key)
        await self.note_writer.delete(_note(meeting))
        async with self.session_factory() as session:
            await MeetingRepository(session).delete_meeting(meeting_id)

    async def _load_meeting(self, meeting_id: uuid.UUID) -> Meeting | None:
        async with self.session_factory() as session:
            return await MeetingRepository(session).get_meeting(meeting_id)

    async def _update(self, meeting_id: uuid.UUID, **fields: object) -> None:
        async with self.session_factory() as session:
            await MeetingRepository(session).update_meeting(meeting_id, **fields)

    async def _replace_segments(
        self,
        meeting_id: uuid.UUID,
        segments: list[Segment],
    ) -> None:
        async with self.session_factory() as session:
            await MeetingRepository(session).replace_segments(meeting_id, segments)

    async def _get_segments(self, meeting_id: uuid.UUID) -> list[Segment]:
        async with self.session_factory() as session:
            return await MeetingRepository(session).get_segments(meeting_id)

    async def _apply_translations(
        self,
        meeting_id: uuid.UUID,
        translations: dict[int, str],
    ) -> None:
        async with self.session_factory() as session:
            await MeetingRepository(session).apply_translations(
                meeting_id, translations
            )

    async def _save_minutes(
        self,
        meeting_id: uuid.UUID,
        draft: MinutesDraft,
    ) -> Minutes:
        # Do not claim a real model produced fake minutes.
        model = draft.model or "fake"
        async with self.session_factory() as session:
            return await MeetingRepository(session).upsert_minutes(
                meeting_id,
                draft,
                model=model,
                prompt_version=self.config.prompt_version,
            )

    async def _record_usage(
        self,
        meeting_id: uuid.UUID,
        usage: Usage | None,
    ) -> None:
        if usage is None:
            return
        async with self.session_factory() as session:
            await MeetingRepository(session).add_usage(meeting_id, usage)

    async def _mark_job_running(self, meeting_id: uuid.UUID) -> uuid.UUID:
        async with self.session_factory() as session:
            repository = MeetingRepository(session)
            job = await repository.upsert_job(meeting_id, "process")
            await repository.update_job(job.id, "running")
            return job.id

    async def _mark_failed(
        self,
        meeting_id: uuid.UUID,
        job_id: uuid.UUID | None,
        exc: BaseException,
    ) -> None:
        message = f"{type(exc).__name__}: {exc}"
        try:
            await self._update(meeting_id, status="failed", error=message)
            if job_id is None:
                async with self.session_factory() as session:
                    job = await MeetingRepository(session).upsert_job(
                        meeting_id, "process"
                    )
                    job_id = job.id
            async with self.session_factory() as session:
                await MeetingRepository(session).update_job(
                    job_id, "failed", error=message
                )
        except Exception:  # noqa: BLE001
            logger.exception("failed to record meeting failure: %s", meeting_id)

    def _render_markdown(
        self,
        meeting: Meeting,
        minutes: Minutes | None,
    ) -> str:
        lines = [
            f"# {meeting.title}",
            "",
            f"- Date: {meeting.created_at.isoformat()}",
            f"- Language: {meeting.local_language}",
            "",
        ]
        if minutes is None:
            lines.append("_Minutes have not been generated yet._")
            return "\n".join(lines) + "\n"

        lines.extend(["## Summary", "", minutes.summary])
        if minutes.topics:
            lines.extend(["", "## Topics"])
            for topic in minutes.topics:
                lines.extend(["", f"### {topic.get('title', '')}"])
                lines.extend(f"- {point}" for point in topic.get("points", []))
        if minutes.decisions:
            lines.extend(["", "## Decisions"])
            for decision in minutes.decisions:
                context = decision.get("context")
                suffix = f" ({context})" if context else ""
                lines.append(f"- {decision.get('text', '')}{suffix}")
        if minutes.action_items:
            lines.extend(["", "## Action items"])
            for item in minutes.action_items:
                owner = item.get("owner") or "unassigned"
                due = item.get("due_date") or "no due date"
                lines.append(
                    f"- [ ] {item.get('text', '')} (owner: {owner}, due: {due})"
                )
        if minutes.highlights:
            lines.extend(["", "## Highlights"])
            for highlight in minutes.highlights:
                lines.append(
                    f"- [{highlight.get('segment_seq')}] {highlight.get('text', '')}"
                )
        return "\n".join(lines) + "\n"

    def _render_txt(
        self,
        meeting: Meeting,
        minutes: Minutes | None,
    ) -> str:
        lines = [
            meeting.title,
            f"Date: {meeting.created_at.isoformat()}",
            f"Language: {meeting.local_language}",
            "",
        ]
        if minutes is None:
            lines.append("Minutes have not been generated yet.")
            return "\n".join(lines) + "\n"

        lines.extend(["Summary", minutes.summary])
        if minutes.topics:
            lines.extend(["", "Topics"])
            for topic in minutes.topics:
                lines.append(f"- {topic.get('title', '')}")
                lines.extend(f"  - {point}" for point in topic.get("points", []))
        if minutes.decisions:
            lines.extend(["", "Decisions"])
            for decision in minutes.decisions:
                lines.append(f"- {decision.get('text', '')}")
        if minutes.action_items:
            lines.extend(["", "Action items"])
            for item in minutes.action_items:
                lines.append(
                    f"- [ ] {item.get('text', '')} "
                    f"(owner: {item.get('owner') or 'unassigned'}, "
                    f"due: {item.get('due_date') or 'no due date'})"
                )
        return "\n".join(lines) + "\n"


def _note(meeting: Meeting) -> MeetingNote:
    return MeetingNote(
        id=meeting.id,
        title=meeting.title,
        local_language=meeting.local_language,
        created_at=meeting.created_at,
    )


def _note_content(draft: MinutesDraft) -> NoteContent:
    return NoteContent(
        summary=draft.summary,
        topics=[
            NoteTopic(title=topic.title, points=list(topic.points))
            for topic in draft.topics
        ],
        decisions=[
            NoteDecision(text=decision.text, context=decision.context)
            for decision in draft.decisions
        ],
        action_items=[
            NoteActionItem(
                text=item.text,
                owner=item.owner,
                due_date=item.due_date,
            )
            for item in draft.action_items
        ],
        highlights=[
            NoteHighlight(segment_seq=item.segment_seq, text=item.text)
            for item in draft.highlights
        ],
    )


def _same_language(detected: str, local: str) -> bool:
    """Compare base language tags so zh and zh-CN count as the same language."""
    return detected.split("-", 1)[0].lower() == local.split("-", 1)[0].lower()
