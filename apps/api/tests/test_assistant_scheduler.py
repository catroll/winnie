from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from winnie_assistant.scheduler import (
    AssistantScheduler,
    TaskDefinitionError,
    TaskStore,
    parse_schedule,
    read_task_runs,
)
from winnie_assistant.types import ChatReply

TZ = ZoneInfo("Asia/Shanghai")
BRIEF = {
    "id": "brief",
    "title": "Morning brief",
    "schedule": "daily 07:30",
    "prompt": "Say hi",
    "enabled": True,
    "webhook": "",
    "created_at": "2026-11-01T00:00:00+08:00",
}


def _write_tasks(tmp_path, tasks):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps({"version": 1, "tasks": tasks}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _scheduler(tmp_path, clock, calls, *, catch_up=3600, http=None, tasks=None):
    path = _write_tasks(tmp_path, tasks or [])

    async def runner(task):
        calls.append(task.id)
        return ChatReply(text=f"done {task.id}", conversation_id="20260101T000000Z-abcdef")

    return AssistantScheduler(
        runner=runner,
        tasks=TaskStore(path),
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
    scheduler = _scheduler(tmp_path, clock, calls, tasks=[BRIEF])

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
    scheduler = _scheduler(tmp_path, clock, calls, tasks=[BRIEF])

    await scheduler.start()
    assert await scheduler.tick(clock[0]) == []
    assert calls == []
    state = scheduler.state_for("brief")
    assert state["last_status"] == "skipped"
    assert state["next_run"] == "2026-11-02T07:30:00+08:00"


async def test_manual_run_does_not_move_the_schedule(tmp_path) -> None:
    clock = [datetime(2026, 11, 1, 7, 0, tzinfo=TZ)]
    calls: list[str] = []
    scheduler = _scheduler(tmp_path, clock, calls, tasks=[BRIEF])
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
    task = {**BRIEF, "webhook": "https://hook.example/brief"}
    scheduler = _scheduler(tmp_path, clock, [], http=client, tasks=[task])
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
    tasks = [
        {"id": "Broken Id", "schedule": "daily 07:30", "prompt": "nope"},
        {"id": "second", "schedule": "hourly", "prompt": "nope"},
        BRIEF,
    ]
    scheduler = _scheduler(tmp_path, clock, [], tasks=tasks)
    await scheduler.start()
    assert [task.id for task in scheduler.tasks] == ["brief"]
    assert len(scheduler.errors) == 2


async def test_tasks_are_created_and_cancelled_at_runtime(tmp_path) -> None:
    clock = [datetime(2026, 11, 1, 7, 0, tzinfo=TZ)]
    calls: list[str] = []
    scheduler = _scheduler(tmp_path, clock, calls)
    await scheduler.start()

    task, next_run = await scheduler.create_task(
        title="喝水提醒",
        schedule="daily 07:30",
        prompt="提醒我喝水",
    )
    assert next_run == "2026-11-01T07:30:00+08:00"
    assert scheduler.find(task.id) is not None

    stored = json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))
    assert stored["tasks"][0]["id"] == task.id
    assert stored["tasks"][0]["title"] == "喝水提醒"

    clock[0] = datetime(2026, 11, 1, 7, 31, tzinfo=TZ)
    runs = await scheduler.tick(clock[0])
    assert [run.task_id for run in runs] == [task.id]
    assert calls == [task.id]

    assert await scheduler.delete_task(task.id) is True
    assert scheduler.find(task.id) is None
    assert scheduler.state_for(task.id) == {}
    assert await scheduler.delete_task(task.id) is False
    stored = json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))
    assert stored["tasks"] == []


async def test_creating_a_task_with_a_bad_schedule_is_rejected(tmp_path) -> None:
    clock = [datetime(2026, 11, 1, 7, 0, tzinfo=TZ)]
    scheduler = _scheduler(tmp_path, clock, [])
    await scheduler.start()
    with pytest.raises(TaskDefinitionError):
        await scheduler.create_task(
            title="bad", schedule="hourly", prompt="nope"
        )
    with pytest.raises(TaskDefinitionError):
        await scheduler.create_task(title="bad", schedule="daily 07:30", prompt="")


def _run_line(task_id, status, started, finished, **extra):
    return json.dumps(
        {
            "task_id": task_id,
            "status": status,
            "started_at": started,
            "finished_at": finished,
            "conversation_id": "",
            "error": "",
            "delivery_error": "",
            **extra,
        }
    )


def test_run_history_is_paged_newest_first_and_skips_bad_lines(tmp_path) -> None:
    path = tmp_path / "runs.jsonl"
    lines = [
        _run_line("brief", "error", "2026-11-01T07:30:00+08:00", "2026-11-01T07:30:05+08:00"),
        "not json",
        _run_line("brief", "ok", "2026-11-02T07:30:00+08:00", "2026-11-02T07:30:03+08:00"),
        _run_line("other", "ok", "2026-11-02T09:00:00+08:00", "2026-11-02T09:00:01+08:00"),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    newest = read_task_runs(path, task_id="brief", limit=1)
    assert newest.total == 2
    assert newest.skipped == 1
    assert [run.status for run in newest.runs] == ["ok"]

    older = read_task_runs(path, task_id="brief", limit=1, offset=1)
    assert [run.status for run in older.runs] == ["error"]
    assert older.total == 2

    everything = read_task_runs(path, limit=10)
    assert everything.total == 3
    assert [run.task_id for run in everything.runs] == ["other", "brief", "brief"]

    assert read_task_runs(path, task_id="missing").total == 0
    assert read_task_runs(tmp_path / "missing.jsonl").runs == []
