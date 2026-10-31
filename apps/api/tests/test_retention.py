from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from winnie_meetings.models import Meeting, utcnow
from winnie_api.runtime import build_runtime
from winnie_api.settings import Settings


def _with_retention(settings: Settings, days: int) -> Settings:
    return settings.model_copy(
        update={
            "meetings": settings.meetings.model_copy(
                update={"audio_retention_days": days}
            )
        }
    )


async def test_purge_expired_audio_keeps_the_meeting(meeting_settings: Settings) -> None:
    settings = _with_retention(meeting_settings, 30)
    runtime = build_runtime(settings)
    assert runtime is not None and runtime.meeting_service is not None
    service = runtime.meeting_service

    await runtime.startup()
    try:
        meeting = await service.create_meeting(user_id="local", title="Old meeting")

        async def chunks():
            yield b"fake audio"

        await service.upload_audio(
            meeting.id,
            user_id="local",
            filename="sample.wav",
            chunks=chunks(),
        )

        async with runtime.session_factory() as session:
            stored = await session.scalar(
                select(Meeting).where(Meeting.id == meeting.id)
            )
            assert stored is not None and stored.audio_key
            audio_key = stored.audio_key
            stored.status = "ready"
            stored.created_at = utcnow() - timedelta(days=60)
            await session.commit()

        audio_path = service.storage.path_for(audio_key)
        assert audio_path is not None and audio_path.exists()

        purged = await service.purge_expired_audio()
        assert purged == 1
        assert not audio_path.exists()

        kept = await service.get_meeting(meeting.id, user_id="local")
        assert kept.audio_key is None
        assert kept.audio_deleted_at is not None
    finally:
        await runtime.shutdown()


async def test_retention_zero_keeps_audio(meeting_settings: Settings) -> None:
    settings = _with_retention(meeting_settings, 0)
    runtime = build_runtime(settings)
    assert runtime is not None and runtime.meeting_service is not None
    service = runtime.meeting_service

    await runtime.startup()
    try:
        meeting = await service.create_meeting(user_id="local", title="Keep audio")

        async def chunks():
            yield b"fake audio"

        await service.upload_audio(
            meeting.id,
            user_id="local",
            filename="sample.wav",
            chunks=chunks(),
        )
        async with runtime.session_factory() as session:
            stored = await session.scalar(
                select(Meeting).where(Meeting.id == meeting.id)
            )
            assert stored is not None and stored.audio_key
            audio_key = stored.audio_key
            stored.status = "ready"
            stored.created_at = utcnow() - timedelta(days=60)
            await session.commit()

        audio_path = service.storage.path_for(audio_key)
        assert audio_path is not None and audio_path.exists()
        assert await service.purge_expired_audio() == 0
        assert audio_path.exists()
    finally:
        await runtime.shutdown()
