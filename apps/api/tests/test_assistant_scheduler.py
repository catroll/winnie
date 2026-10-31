from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from winnie_assistant.agent import ChatReply
from winnie_assistant.scheduler import (
    AssistantScheduler,
    TaskDefinitionError,
    parse_schedule,
)

TZ = ZoneInfo("Asia/Shanghai")
TASKS = """\
timezone: Asia/Shanghai
tasks:
  - id: brief
    title: Morning brief
    schedule: daily 07:30
    prompt: Say hi
    enabled: true
"""


def _scheduler(tmp_path, clock, calls, *, catch_up=3600, http=None, tasks=TASKS):
    tasks_path = tmp_path / "tasks.yaml"
    tasks_path.write_text(tasks, encoding="utf-8")

    async def runner(task):
        calls.append(task.id)
        return ChatReply(text=f"done {task.id}", conversation_id="20260101T000000Z-abcdef")

    return AssistantScheduler(
        runner=runner,
        tasks_path=tasks_path,
        state_path=tmp_path / "state.json",
        runs_path=tmp_path / "tasks" / "runs.jsonl",
        timezone=TZ,
        tick_seconds=30,
        catch_up_seconds=catch_up,
        http=http,
        clock=lambda: clock[0],
        background=False,
    )


def test_parse_schedule_forms() -> None:
    now = datetime(2026, 11, 1, 7, 0, tzinfo=TZ)  # Sunday

    assert parse_schedule("every 15m").next_after(now) == now + timedelta(minutes=15)
    assert parse_schedule("every 2h").next_after(now) == now + timedelta(hours=2)
    assert parse_schedule("daily 07:30").next_after(now) == now.replace(
        hour=7, minute=30
    )
    assert parse_schedule("daily 07:30").next_after(now.replace(hour=8)) == now.replace(
        day=2, hour=7, minute=30
    )
    assert parse_schedule("weekly mon 09:00").next_after(now) == now.replace(
        day=2, hour=9, minute=0
    )
    assert parse_schedule("weekly sun 09:00").next_after(now) == now.replace(
        hour=9, minute=0
    )

    for value in ("every 1m", "*/5 * * * *", "daily 25:00", "weekly xyz 09:00"):
        with pytest.raises(TaskDefinitionError):
            parse_schedule(value)


async def test_daily_task_runs_once_and_advances(tmp_path) -> None:
    clock = [datetime(2026, 11, 1, 7, 0, tzinfo=TZ)]
    calls: list[str] = []
    scheduler = _scheduler(tmp_path, clock, calls)

    await scheduler.start()
    assert scheduler.state_for("brief")["next_run"] == "2026-11-01T07:30:00+08:00"
    assert await scheduler.tick(clock[0]) == []

    clock[0] = datetime(2026, 11, 1, 7, 31, tzinfo=TZ)
    runs = await scheduler.tick(clock[0])
    assert [run.status for run in runs] == ["ok"]
    assert calls == ["brief"]
    assert scheduler.state_for("brief")["next_run"] == "2026-11-02T07:30:00+08:00"

    assert await scheduler.tick(clock[0]) == []
    lines = (tmp_path / "tasks" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert record["status"] == "ok"
    assert record["conversation_id"] == "20260101T000000Z-abcdef"


async def test_a_missed_run_is_skipped_instead_of_replayed(tmp_path) -> None:
    (tmp_path / "state.json").write_text(
        json.dumps({"tasks": {"brief": {"next_run": "2026-11-01T07:30:00+08:00"}}}),
        encoding="utf-8",
    )
    clock = [datetime(2026, 11, 1, 10, 31, tzinfo=TZ)]
    calls: list[str] = []
    scheduler = _scheduler(tmp_path, clock, calls)

    await scheduler.start()
    assert await scheduler.tick(clock[0]) == []
    assert calls == []
    state = scheduler.state_for("brief")
    assert state["last_status"] == "skipped"
    assert state["next_run"] == "2026-11-02T07:30:00+08:00"


async def test_manual_run_does_not_move_the_schedule(tmp_path) -> None:
    clock = [datetime(2026, 11, 1, 7, 0, tzinfo=TZ)]
    calls: list[str] = []
    scheduler = _scheduler(tmp_path, clock, calls)
    await scheduler.start()
    before = scheduler.state_for("brief")["next_run"]

    run = await scheduler.run_now("brief")
    assert run.status == "ok"
    assert scheduler.state_for("brief")["next_run"] == before

    with pytest.raises(KeyError):
        await scheduler.run_now("missing")


async def test_webhook_delivery_posts_the_reply(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    clock = [datetime(2026, 11, 1, 7, 31, tzinfo=TZ)]
    tasks = TASKS + '    webhook: https://hook.example/brief\n'
    scheduler = _scheduler(tmp_path, clock, [], http=client, tasks=tasks)
    try:
        await scheduler.start()
        run = await scheduler.run_now("brief")
    finally:
        await client.aclose()

    assert run.delivery_error == ""
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert requests[0].url == "https://hook.example/brief"
    assert body["task_id"] == "brief"
    assert body["text"] == "done brief"


async def test_invalid_tasks_are_reported_not_fatal(tmp_path) -> None:
    clock = [datetime(2026, 11, 1, 7, 0, tzinfo=TZ)]
    tasks = TASKS + (
        "  - id: Broken Id\n    schedule: daily 07:30\n    prompt: nope\n"
        "  - id: second\n    schedule: hourly\n    prompt: nope\n"
    )
    scheduler = _scheduler(tmp_path, clock, [], tasks=tasks)
    await scheduler.start()
    assert [task.id for task in scheduler.tasks] == ["brief"]
    assert len(scheduler.errors) == 2
