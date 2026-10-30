from __future__ import annotations

import uuid

from winnie_api.jobs.queue import ArqJobQueue


async def test_arq_queue_enqueues_and_closes_the_pool(monkeypatch) -> None:
    class FakePool:
        def __init__(self) -> None:
            self.jobs: list[tuple[str, str]] = []
            self.closed = False

        async def enqueue_job(self, name: str, value: str) -> None:
            self.jobs.append((name, value))

        async def aclose(self) -> None:
            self.closed = True

    pool = FakePool()

    async def fake_create_pool(_settings):
        return pool

    monkeypatch.setattr("arq.create_pool", fake_create_pool)
    queue = ArqJobQueue("redis://example:6379/0")
    meeting_id = uuid.uuid4()

    await queue.enqueue(meeting_id)

    assert pool.jobs == [("process_meeting_job", str(meeting_id))]
    assert pool.closed is True
