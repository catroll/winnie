from __future__ import annotations

import uuid

import pytest

from winnie_api.jobs.tasks import (
    cleanup_expired_audio,
    cleanup_expired_sessions,
    process_meeting_job,
)


class FakeMeetingService:
    def __init__(self) -> None:
        self.meeting_id: uuid.UUID | None = None

    async def process(self, meeting_id: uuid.UUID) -> None:
        self.meeting_id = meeting_id


class FakeAuthService:
    async def cleanup_expired_sessions(self) -> int:
        return 3


async def test_cleanup_task_uses_auth_service() -> None:
    assert await cleanup_expired_sessions({"auth": FakeAuthService()}) == 3


async def test_cleanup_task_requires_auth() -> None:
    with pytest.raises(RuntimeError, match="accounts are not enabled"):
        await cleanup_expired_sessions({})


async def test_meeting_task_passes_a_uuid() -> None:
    service = FakeMeetingService()
    meeting_id = uuid.uuid4()
    await process_meeting_job({"service": service}, str(meeting_id))
    assert service.meeting_id == meeting_id


async def test_meeting_task_requires_service() -> None:
    with pytest.raises(RuntimeError, match="meetings are not enabled"):
        await process_meeting_job({}, str(uuid.uuid4()))


async def test_audio_cleanup_task_uses_meeting_service() -> None:
    class FakeService:
        async def purge_expired_audio(self) -> int:
            return 4

    assert await cleanup_expired_audio({"service": FakeService()}) == 4


async def test_audio_cleanup_task_requires_meetings() -> None:
    with pytest.raises(RuntimeError, match="meetings are not enabled"):
        await cleanup_expired_audio({})
