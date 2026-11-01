"""The proactive engine: planning, policy, delivery and acknowledgement.

The point of these tests is the properties a reminder must have and a chat
prompt cannot: the same scan twice creates one item, quiet hours hold it, the
budget demotes it, a send that is not acknowledged escalates, and an ack stops
the ladder.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from winnie_core.db import Base, create_session_factory
from winnie_proactive import models  # noqa: F401 - registers the table
from winnie_proactive.config import (
    ChannelsConfig,
    EscalationConfig,
    ProactiveConfig,
)
from winnie_proactive.ports import Message
from winnie_proactive.service import ProactiveService

OWNER = "owner-1"


class FakeCalendar:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def upcoming(self, *, since, until):
        return self.rows


class FakeContacts:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def all(self):
        return self.rows


class FakeSchedule:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def events(self, *, since, until):
        return [
            row
            for row in self.rows
            if since <= date.fromisoformat(row["date"]) <= until
        ]


class RecordingChannel:
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.sent: list[Message] = []

    async def send(self, message: Message) -> str:
        if self.fail:
            from winnie_proactive.ports import ChannelError

            raise ChannelError(f"{self.name} is down")
        self.sent.append(message)
        return f"{self.name} ok"


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


def birthday_row(
    title: str = "圆圆生日",
    *,
    on: date = date(2026, 12, 20),
    recurrence: str = "FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=12",
    calendar_name: str = "Phone",
) -> dict:
    return {
        "id": "b-1",
        "title": title,
        "calendar_name": calendar_name,
        "starts_at": datetime(on.year, on.month, on.day, tzinfo=UTC),
        "recurrence": recurrence,
    }


@pytest.fixture
async def engine(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'proactive.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


class FakeComposer:
    def __init__(self, text: str = "记得先订蛋糕，再确认当天有没有安排。", *, fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0

    async def compose(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("the model is down")
        return self.text


def build(
    tmp_path: Path,
    sessions: async_sessionmaker,
    *,
    clock: Clock,
    calendar=None,
    contacts=None,
    schedule=None,
    channels: dict | None = None,
    max_interrupts: int = 4,
    composer=None,
) -> ProactiveService:
    config = ProactiveConfig(
        enabled=True,
        timezone="Asia/Shanghai",
        root=str(tmp_path),
        catch_up_hours=48,
        max_interrupts_per_day=max_interrupts,
        quiet_hours=("22:30", "07:30"),
        channels=ChannelsConfig(order=["push", "ntfy", "email"]),
        escalation=EscalationConfig(after_minutes=[20, 60]),
    )

    async def owner() -> str:
        return OWNER

    return ProactiveService(
        config,
        session_factory=sessions,
        channels=channels or {"push": RecordingChannel("push")},
        resolve_owner=owner,
        root=tmp_path,
        calendar=calendar,
        contacts=contacts,
        schedule=schedule,
        composer=composer,
        clock=clock,
    )


def sessions_for(engine) -> async_sessionmaker:
    return create_session_factory(engine)


@pytest.mark.asyncio
async def test_scan_creates_one_item_per_stage_and_is_idempotent(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 11, 2, 1, 0, tzinfo=UTC))  # 09:00 Shanghai
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        calendar=FakeCalendar([birthday_row()]),
        contacts=FakeContacts([]),
    )

    assert await service.scan() == 5
    assert await service.scan() == 0

    items = await service.items(OWNER)
    assert len(items) == 5
    stages = {item["stage"]: item for item in items}
    assert set(stages) == {"heads_up", "prepare", "confirm", "day", "follow_up"}
    assert stages["heads_up"]["priority"] == 2
    assert stages["day"]["priority"] == 1
    assert stages["follow_up"]["priority"] == 3
    assert stages["day"]["planned_at"] == datetime(
        2026, 12, 20, 1, 0, tzinfo=UTC
    )  # 09:00 Shanghai
    assert stages["day"]["evidence"][0]["title"] == "圆圆生日"


@pytest.mark.asyncio
async def test_the_budget_is_spent_before_a_fifth_interrupt(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 12, 20, 1, 0, tzinfo=UTC))  # day stage is due
    channel = RecordingChannel("push")
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        calendar=FakeCalendar([birthday_row()]),
        contacts=FakeContacts([]),
        channels={"push": channel},
    )
    await service.scan()
    await service.dispatch()

    assert len(channel.sent) == 1
    assert "今天" in channel.sent[0].title


@pytest.mark.asyncio
async def test_quiet_hours_hold_a_reminder_until_the_morning(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 12, 20, 15, 30, tzinfo=UTC))  # 23:30 Shanghai
    channel = RecordingChannel("push")
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        calendar=FakeCalendar([birthday_row()]),
        contacts=FakeContacts([]),
        channels={"push": channel},
    )
    await service.scan()
    await service.dispatch()

    assert channel.sent == []
    item = next(
        row for row in await service.items(OWNER) if row["stage"] == "day"
    )
    # 07:30 the next morning, Shanghai.
    assert item["planned_at"] == datetime(2026, 12, 20, 23, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_an_unacknowledged_send_walks_the_ladder_and_an_ack_stops_it(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 12, 20, 1, 0, tzinfo=UTC))
    push, ntfy = RecordingChannel("push"), RecordingChannel("ntfy")
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        calendar=FakeCalendar([birthday_row()]),
        contacts=FakeContacts([]),
        channels={"push": push, "ntfy": ntfy},
    )
    await service.scan()
    await service.dispatch()
    assert len(push.sent) == 1

    clock.moment += timedelta(minutes=20)
    assert await service.escalate() == 1
    assert len(ntfy.sent) == 1
    assert push.sent[0].title == ntfy.sent[0].title

    item = next(
        row for row in await service.items(OWNER) if row["stage"] == "day"
    )
    acked = await service.ack(OWNER, item["id"], via="test")
    assert acked["status"] == "acked"

    # Nothing is left to escalate.
    clock.moment += timedelta(hours=2)
    assert await service.escalate() == 0
    assert len(ntfy.sent) == 1


@pytest.mark.asyncio
async def test_a_dead_channel_is_skipped_in_the_same_delivery(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 12, 20, 1, 0, tzinfo=UTC))
    dead, live = RecordingChannel("push", fail=True), RecordingChannel("email")
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        calendar=FakeCalendar([birthday_row()]),
        contacts=FakeContacts([]),
        channels={"push": dead, "email": live},
    )
    await service.scan()
    await service.dispatch()

    assert len(live.sent) == 1
    item = next(
        row for row in await service.items(OWNER) if row["stage"] == "day"
    )
    names = [entry["name"] for entry in item["channels"]]
    assert names == ["push", "email"]
    assert item["channels"][0]["error"]


@pytest.mark.asyncio
async def test_a_person_with_two_dates_gets_a_conflict_item_and_both_stages(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 11, 2, 1, 0, tzinfo=UTC))
    rows = [
        birthday_row("圆圆两岁生日", on=date(2026, 12, 12), recurrence=""),
        birthday_row(on=date(2026, 12, 20)),
    ]
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        calendar=FakeCalendar(rows),
        contacts=FakeContacts([]),
    )
    await service.scan()

    items = await service.items(OWNER)
    conflicts = [item for item in items if item["kind"] == "conflict"]
    assert len(conflicts) == 1
    assert "两个生日日期" in conflicts[0]["title"]
    # Both dates still get their stages: silence on the motivating case would be
    # worse than asking while the reminders run.
    assert {item["occurrence_key"] for item in items if item["kind"] == "birthday"} == {
        "2026-12-12",
        "2026-12-20",
    }


@pytest.mark.asyncio
async def test_a_backlog_collapses_to_the_most_recent_stage(
    tmp_path: Path, engine
) -> None:
    # 12-14 is after T-14 and T-7 were due; only the newest past stage survives.
    clock = Clock(datetime(2026, 12, 14, 1, 0, tzinfo=UTC))
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        calendar=FakeCalendar([birthday_row()]),
        contacts=FakeContacts([]),
    )
    await service.scan()

    stages = {item["stage"] for item in await service.items(OWNER)}
    assert "heads_up" not in stages
    assert "prepare" in stages
    assert "day" in stages


@pytest.mark.asyncio
async def test_the_digest_collects_planned_digest_items_and_unacked_sends(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 11, 2, 1, 0, tzinfo=UTC))  # 09:00 Shanghai
    channel = RecordingChannel("push")
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        calendar=FakeCalendar([birthday_row(on=date(2026, 11, 16))]),
        contacts=FakeContacts([]),
        channels={"push": channel},
    )
    # T-14 is planned for 09:00 today; it is a digest-priority stage.
    await service.scan()

    # 20:30 Shanghai, the evening slot.
    clock.moment = datetime(2026, 11, 2, 12, 30, tzinfo=UTC)
    assert await service.digest() == 1
    assert len(channel.sent) == 1
    assert "晚上好" in channel.sent[0].title
    items = await service.items(OWNER)
    assert any(item["kind"] == "digest" for item in items)
    assert any(item["status"] == "digested" for item in items)
    # Once per slot.
    assert await service.digest() == 0


@pytest.mark.asyncio
async def test_work_items_are_detected_from_the_schedule(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 11, 2, 2, 0, tzinfo=UTC))  # 10:00 Shanghai
    schedule = FakeSchedule(
        [
            {
                "id": "s-1",
                "date": "2026-11-02",
                "start": "15:50",
                "text": "带图图去知了上课",
                "done": False,
                "scope": "day",
            },
            {
                "id": "s-2",
                "date": "2026-10-30",
                "start": "10:00",
                "text": "买水果",
                "done": False,
                "scope": "day",
            },
        ]
    )
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        schedule=schedule,
    )
    await service.scan()

    items = {item["kind"]: item for item in await service.items(OWNER)}
    assert items["leave_by"]["title"].startswith("15:50")
    assert "带图图去知了上课" in items["leave_by"]["title"]
    assert items["overdue"]["priority"] == 2
    assert "买水果" in items["overdue"]["body"]


@pytest.mark.asyncio
async def test_an_email_ack_token_stops_the_item(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 12, 20, 1, 0, tzinfo=UTC))
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        calendar=FakeCalendar([birthday_row()]),
        contacts=FakeContacts([]),
    )
    await service.scan()
    await service.dispatch()
    item = next(
        row for row in await service.items(OWNER) if row["stage"] == "day"
    )

    import uuid

    from sqlalchemy import select

    async with sessions_for(engine)() as session:
        row = await session.scalar(
            select(models.ProactiveItem).where(
                models.ProactiveItem.id == uuid.UUID(item["id"])
            )
        )
        token = row.ack_token

    acked = await service.ack_by_token(token)
    assert acked is not None
    assert acked["status"] == "acked"


@pytest.mark.asyncio
async def test_a_test_item_is_delivered_and_can_be_acknowledged(
    tmp_path: Path, engine
) -> None:
    """The console's test button must produce a real item, not a bare message.

    Only a real item carries an id in the notification payload, so only a real
    item can exercise the phone's 收到 action.
    """
    clock = Clock(datetime(2026, 11, 2, 1, 0, tzinfo=UTC))
    push = RecordingChannel("push")
    service = build(
        tmp_path, sessions_for(engine), clock=clock, channels={"push": push}
    )

    result = await service.test_message()

    assert result["delivered"] is True
    assert result["item"]["kind"] == "test"
    assert result["channels"][0]["name"] == "push"
    assert push.sent[-1].data["item_id"] == result["item"]["id"]
    acked = await service.ack(OWNER, result["item"]["id"], via="test")
    assert acked["status"] == "acked"


@pytest.mark.asyncio
async def test_a_targeted_test_names_the_channel_that_failed(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 11, 2, 1, 0, tzinfo=UTC))
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        channels={"ntfy": RecordingChannel("ntfy", fail=True)},
    )

    result = await service.test_message(channel="ntfy")

    assert result["delivered"] is False
    assert "down" in result["error"]


@pytest.mark.asyncio
async def test_a_channel_that_is_not_configured_is_reported(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 11, 2, 1, 0, tzinfo=UTC))
    service = build(
        tmp_path, sessions_for(engine), clock=clock, channels={"push": RecordingChannel("push")}
    )

    with pytest.raises(Exception):
        await service.test_message(channel="email")


@pytest.mark.asyncio
async def test_the_composer_rewrites_once_and_the_ladder_reuses_it(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 12, 20, 1, 0, tzinfo=UTC))
    push, ntfy = RecordingChannel("push"), RecordingChannel("ntfy")
    composer = FakeComposer()
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        calendar=FakeCalendar([birthday_row()]),
        contacts=FakeContacts([]),
        channels={"push": push, "ntfy": ntfy},
        composer=composer,
    )
    await service.scan()
    await service.dispatch()

    assert composer.calls == 1
    assert push.sent[0].body == composer.text

    clock.moment += timedelta(minutes=20)
    await service.escalate()

    assert ntfy.sent[0].body == composer.text
    assert composer.calls == 1


@pytest.mark.asyncio
async def test_a_failed_composer_leaves_the_template_and_is_not_retried(
    tmp_path: Path, engine
) -> None:
    clock = Clock(datetime(2026, 12, 20, 1, 0, tzinfo=UTC))
    push = RecordingChannel("push")
    composer = FakeComposer(fail=True)
    service = build(
        tmp_path,
        sessions_for(engine),
        clock=clock,
        calendar=FakeCalendar([birthday_row()]),
        contacts=FakeContacts([]),
        channels={"push": push},
        composer=composer,
    )
    await service.scan()
    await service.dispatch()

    assert composer.calls == 1
    assert "今天" in push.sent[0].title
    assert "上午发条消息" in push.sent[0].body
