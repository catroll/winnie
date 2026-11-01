"""Phone tools: formatting, proposals, the prompt digest and skill gating."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from winnie_assistant.search import FakeSearchProvider
from winnie_assistant.skills import PHONE, PHONE_WRITE, ToolboxSkill
from winnie_assistant.tools import ToolBox
from winnie_chat import SkillRegistry

NOW = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
SHANGHAI = ZoneInfo("Asia/Shanghai")


class StubPhone:
    """An in-memory gateway with the shape of the real adapter."""

    def __init__(self) -> None:
        self.proposals: list[tuple[str, dict[str, Any]]] = []

    async def summary(self) -> dict[str, Any]:
        return {
            "devices": [
                {
                    "device": {
                        "id": "dev-1",
                        "name": "Pixel 8",
                        "model": "Pixel 8",
                        "os_version": "17",
                        "app_version": "0.1.0",
                        "timezone": "Asia/Shanghai",
                        "scopes": ["calendar", "message", "call"],
                        "revoked": False,
                        "last_sync_at": NOW - timedelta(minutes=3),
                    },
                    "counts": [
                        {"kind": "calendar", "count": 12},
                        {"kind": "message", "count": 340},
                        {"kind": "call", "count": 21},
                    ],
                }
            ],
            "last_sync_at": NOW - timedelta(minutes=3),
            "stale_minutes": 3,
            "pending_commands": 1,
        }

    async def search(self, query, *, kinds=None, since=None, until=None, limit=20):
        return [
            {
                "kind": "message",
                "at": NOW - timedelta(minutes=30),
                "title": "SMS inbox with +8613800138000",
                "text": "the dentist moved my appointment",
                "device_name": "Pixel 8",
                "record_id": "9",
            }
        ]

    async def calendar(self, *, since=None, until=None, query="", limit=50):
        return [
            {
                "id": "501",
                "device_name": "Pixel 8",
                "calendar_name": "Work",
                "title": "Dentist",
                "description": "",
                "location": "Clinic",
                "starts_at": NOW.replace(hour=7, minute=0),
                "ends_at": NOW.replace(hour=8, minute=0),
                "all_day": False,
                "timezone": "Asia/Shanghai",
                "organizer": "",
                "attendees": [],
                "status": "confirmed",
            }
        ]

    async def calls(self, *, since=None, until=None, direction="", query="", limit=50):
        return [
            {
                "id": "601",
                "device_name": "Pixel 8",
                "number": "+8613900139000",
                "cached_name": "Zhang",
                "direction": direction or "missed",
                "started_at": NOW - timedelta(hours=1),
                "duration_seconds": 0,
                "is_new": True,
            }
        ]

    async def messages(
        self,
        *,
        since=None,
        until=None,
        address="",
        query="",
        unread_only=False,
        limit=50,
    ):
        return [
            {
                "id": "701",
                "device_name": "Pixel 8",
                "address": "+8613800138000",
                "direction": "inbox",
                "body": "pick up the parcel",
                "subject": "",
                "received_at": NOW - timedelta(minutes=40),
                "is_read": False,
                "kind": "sms",
            }
        ]

    async def contacts(self, *, query="", limit=50):
        return []

    async def notifications(self, *, since=None, until=None, app="", query="", limit=50):
        return [
            {
                "id": "801",
                "device_name": "Pixel 8",
                "package_name": "com.example",
                "app_label": "Health",
                "title": "Reminder",
                "text": "walk 5000 steps",
                "category": "reminder",
                "posted_at": NOW - timedelta(minutes=5),
                "is_ongoing": False,
            }
        ]

    async def locations(self, *, since=None, until=None, limit=100):
        return [
            {
                "id": "901",
                "device_name": "Pixel 8",
                "latitude": 31.2304,
                "longitude": 121.4737,
                "accuracy_m": 12.0,
                "provider": "network",
                "captured_at": NOW - timedelta(minutes=10),
            }
        ]

    async def apps(self, *, query="", limit=100):
        return [
            {
                "id": "com.tencent.mm",
                "device_name": "Pixel 8",
                "package_name": "com.tencent.mm",
                "label": "WeChat",
                "version_name": "8.0.50",
                "is_system": False,
                "is_enabled": True,
                "installed_at": None,
                "updated_at": None,
            }
        ]

    async def propose_command(self, kind, payload):
        self.proposals.append((kind, payload))
        return {
            "id": "cmd-1",
            "kind": kind,
            "summary": "send SMS to +8613800138000: hello",
            "status": "proposed",
            "expires_at": NOW + timedelta(minutes=15),
        }

    async def approve_command(self, command_id):
        return {
            "id": command_id,
            "kind": "sms.send",
            "summary": "send SMS to +8613800138000: hello",
            "status": "approved",
        }

    async def cancel_command(self, command_id):
        return {
            "id": command_id,
            "kind": "sms.send",
            "summary": "send SMS to +8613800138000: hello",
            "status": "cancelled",
        }


@pytest.fixture
def phone() -> StubPhone:
    return StubPhone()


@pytest.fixture
def toolbox(phone: StubPhone) -> ToolBox:
    return ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=httpx.AsyncClient(),
        phone=phone,
        timezone=SHANGHAI,
        clock=lambda: NOW,
    )


async def call(toolbox: ToolBox, name: str, **arguments) -> str:
    return await toolbox.execute(name, arguments, conversation_id="conv")


@pytest.mark.asyncio
async def test_status_reports_device_and_freshness(toolbox: ToolBox) -> None:
    text = await call(toolbox, "phone_status")
    assert "Pixel 8" in text
    assert "api" not in text
    assert "2026-11-01 13:57" in text  # NOW-3min in Asia/Shanghai
    assert "calendar=12" in text
    assert "pending phone commands: 1" in text


@pytest.mark.asyncio
async def test_calendar_shows_local_time_and_event_id(toolbox: ToolBox) -> None:
    text = await call(toolbox, "phone_calendar", days_ahead=2)
    assert "2026-11-01 15:00-16:00" in text
    assert "[id 501]" in text
    assert "Dentist" in text
    assert "@ Clinic" in text
    assert "[Work]" in text


@pytest.mark.asyncio
async def test_calendar_reads_with_the_default_clock(phone: StubPhone) -> None:
    """The factory passes a zone and no clock; the default must work.

    Every other test injects ``clock=lambda: NOW``, which is why a default
    clock that raised on every call went unnoticed until the Android chat
    asked for today's schedule.
    """
    toolbox = ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=httpx.AsyncClient(),
        phone=phone,
        timezone=SHANGHAI,
    )
    now = toolbox.clock()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)

    text = await call(toolbox, "phone_calendar", days_ahead=2)
    assert "Dentist" in text


@pytest.mark.asyncio
async def test_search_renders_hits(toolbox: ToolBox) -> None:
    text = await call(toolbox, "phone_search", query="dentist")
    assert "[message]" in text
    assert "the dentist moved my appointment" in text
    assert "(Pixel 8)" in text


@pytest.mark.asyncio
async def test_messages_mark_unread(toolbox: ToolBox) -> None:
    text = await call(toolbox, "phone_messages", unread_only=True)
    assert "[unread]" in text
    assert "pick up the parcel" in text


@pytest.mark.asyncio
async def test_notifications_locations_and_apps(toolbox: ToolBox) -> None:
    notifications = await call(toolbox, "phone_notifications")
    assert "Health: Reminder" in notifications
    assert "walk 5000 steps" in notifications

    locations = await call(toolbox, "phone_location")
    assert "31.23040,121.47370" in locations
    assert "±12m" in locations

    apps = await call(toolbox, "phone_apps")
    assert "WeChat (com.tencent.mm) 8.0.50" in apps


@pytest.mark.asyncio
async def test_sms_proposal_is_pending_not_sent(
    toolbox: ToolBox, phone: StubPhone
) -> None:
    text = await call(
        toolbox,
        "phone_sms_send",
        address="+8613800138000",
        body="hello",
    )
    assert "PENDING cmd-1" in text
    assert "Nothing has been written yet" in text
    assert "phone_confirm" in text
    assert phone.proposals == [
        ("sms.send", {"address": "+8613800138000", "body": "hello"})
    ]


@pytest.mark.asyncio
async def test_naive_calendar_time_uses_the_assistant_zone(
    toolbox: ToolBox, phone: StubPhone
) -> None:
    text = await call(
        toolbox,
        "phone_calendar_create",
        title="Dentist",
        starts_at="2026-11-02T15:00",
        location="Clinic",
    )
    assert "PENDING cmd-1" in text
    kind, payload = phone.proposals[0]
    assert kind == "calendar.create"
    assert payload["starts_at"] == "2026-11-02T15:00:00+08:00"
    assert payload["location"] == "Clinic"


@pytest.mark.asyncio
async def test_confirm_and_cancel(toolbox: ToolBox) -> None:
    confirmed = await call(toolbox, "phone_confirm", command_id="cmd-1")
    assert confirmed.startswith("APPROVED cmd-1")
    assert "next sync" in confirmed

    cancelled = await call(toolbox, "phone_cancel", command_id="cmd-1")
    assert cancelled.startswith("CANCELLED cmd-1")


@pytest.mark.asyncio
async def test_calendar_update_needs_a_change(toolbox: ToolBox) -> None:
    text = await call(toolbox, "phone_calendar_update", event_id="501")
    assert text.startswith("ERROR:")


@pytest.mark.asyncio
async def test_digest_contains_agenda_and_missed_calls(
    toolbox: ToolBox, phone: StubPhone
) -> None:
    digest = await toolbox.phone_digest(NOW)
    assert "Phone Pixel 8" in digest
    assert "3 min ago" in digest
    assert "Next 24h:" in digest
    assert "15:00 Dentist @ Clinic" in digest
    assert "Unread SMS:" in digest
    assert "Missed calls today:" in digest
    assert "Zhang" in digest


@pytest.mark.asyncio
async def test_without_phone_the_tool_reports_it() -> None:
    toolbox = ToolBox(
        search=FakeSearchProvider(),
        index=None,
        memory=None,
        scheduler=None,
        http=httpx.AsyncClient(),
    )
    text = await call(toolbox, "phone_status")
    assert text == "ERROR: phone data is disabled on the server"
    assert await toolbox.phone_digest(NOW) == ""


def test_write_tools_are_not_offered_to_scheduled_turns(toolbox: ToolBox) -> None:
    registry = SkillRegistry(
        [ToolboxSkill(PHONE, toolbox), ToolboxSkill(PHONE_WRITE, toolbox)]
    )
    scheduled = {
        schema["function"]["name"] for schema in registry.schemas(scheduled=True)
    }
    interactive = {
        schema["function"]["name"] for schema in registry.schemas(scheduled=False)
    }
    assert "phone_calendar" in scheduled
    assert "phone_sms_send" not in scheduled
    assert "phone_confirm" not in scheduled
    assert {"phone_sms_send", "phone_confirm"} <= interactive

    views = {view.name: view for view in registry.views()}
    assert views["phone"].available is True
    assert views["phone_write"].scheduled_safe is False
