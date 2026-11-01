"""Phone sync: upload paths, queries, retention, commands and isolation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from winnie_api.main import create_app
from winnie_api.settings import DatabaseConfig, Settings
from winnie_core.config import AuthConfig, JobsConfig
from winnie_core.db import create_all, create_engine, create_session_factory
from winnie_phone.config import PhoneConfig, PhoneRetentionConfig
from winnie_phone.schemas import DeviceRegister
from winnie_phone.service import InvalidCommand, PhoneService

NOW = datetime.now(timezone.utc)


def iso(**offset) -> str:
    return (NOW - timedelta(**offset)).isoformat()


DEVICE = {
    "device_key": "phone-key-0001",
    "name": "Pixel 8",
    "platform": "android",
    "manufacturer": "Google",
    "model": "Pixel 8",
    "os_version": "17",
    "app_version": "0.1.0",
    "timezone": "Asia/Shanghai",
    "scopes": [
        "calendar",
        "call",
        "message",
        "contact",
        "notification",
        "location",
        "app",
    ],
    "info": {"battery_percent": 80, "network": "wifi"},
}


async def register(
    client: httpx.AsyncClient, *, headers: dict | None = None, **overrides
) -> dict:
    payload = {**DEVICE, **overrides}
    response = await client.post(
        "/api/v1/phone/devices", json=payload, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


async def sync(
    client: httpx.AsyncClient,
    device_id: str,
    kind: str,
    items: list[dict],
    *,
    mode: str = "incremental",
    cursor: str = "",
    deleted: list[str] | None = None,
    device: dict | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> httpx.Response:
    body: dict = {"kind": kind, "mode": mode, "cursor": cursor, "items": items}
    if deleted:
        body["deleted"] = deleted
    if device:
        body["device"] = device
    if since is not None:
        body["since"] = since.isoformat()
    if until is not None:
        body["until"] = until.isoformat()
    return await client.post(
        f"/api/v1/phone/devices/{device_id}/sync", json=body
    )


def message(external_id: str, body: str, **overrides) -> dict:
    return {
        "external_id": external_id,
        "thread_id": "7",
        "address": "+8613800138000",
        "direction": "inbox",
        "body": body,
        "received_at": iso(minutes=10),
        "is_read": False,
        **overrides,
    }


@pytest.mark.asyncio
async def test_register_and_sync_messages(phone_client: httpx.AsyncClient) -> None:
    device = await register(phone_client)
    assert device["name"] == "Pixel 8"
    assert device["states"] == []

    result = await sync(
        phone_client,
        device["id"],
        "message",
        [
            message("101", "first message", received_at=iso(minutes=30)),
            message("102", "second message"),
        ],
        cursor="102",
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["accepted"] == 2
    assert body["cursor"] == "102"
    assert body["interval_minutes"] == 30

    listing = (await phone_client.get("/api/v1/phone/messages")).json()
    assert [row["body"] for row in listing] == ["second message", "first message"]
    assert listing[0]["device_name"] == "Pixel 8"
    assert listing[0]["is_read"] is False

    summary = (await phone_client.get("/api/v1/phone/summary")).json()
    counts = {row["kind"]: row["count"] for row in summary["counts"]}
    assert counts["message"] == 2
    assert summary["stale_minutes"] is not None
    assert summary["devices"][0]["device"]["states"][0]["cursor"] == "102"


@pytest.mark.asyncio
async def test_reupload_updates_in_place(phone_client: httpx.AsyncClient) -> None:
    device = await register(phone_client)
    await sync(phone_client, device["id"], "message", [message("201", "original")])
    await sync(phone_client, device["id"], "message", [message("201", "edited")])

    listing = (await phone_client.get("/api/v1/phone/messages")).json()
    assert len(listing) == 1
    assert listing[0]["body"] == "edited"


async def seed_known_senders(client: httpx.AsyncClient, device_id: str) -> None:
    """Six messages from four senders, with the +86 split real phones produce."""
    response = await sync(
        client,
        device_id,
        "message",
        [
            message(
                "211",
                "received from the mobile",
                address="+8613800138000",
                received_at=iso(minutes=50),
            ),
            message(
                "212",
                "reply to the mobile",
                address="13800138000",
                direction="sent",
                received_at=iso(minutes=40),
            ),
            message(
                "213",
                "carrier notice",
                address="10086",
                received_at=iso(minutes=30),
            ),
            message(
                "214",
                "carrier confirmation",
                address="10086",
                direction="sent",
                received_at=iso(minutes=25),
            ),
            message(
                "215",
                "bulk promotion",
                address="1069000000001",
                received_at=iso(minutes=20),
            ),
        ],
    )
    assert response.status_code == 200, response.text
    response = await sync(
        client,
        device_id,
        "message",
        [
            message(
                "216",
                "bank notice",
                address="95566",
                received_at=iso(minutes=10),
            ),
        ],
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_message_filters_by_direction_and_hidden_numbers(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    await seed_known_senders(phone_client, device["id"])

    only_sent = (
        await phone_client.get("/api/v1/phone/messages", params={"direction": "sent"})
    ).json()
    assert [row["body"] for row in only_sent] == [
        "carrier confirmation",
        "reply to the mobile",
    ]

    only_inbox = (
        await phone_client.get("/api/v1/phone/messages", params={"direction": "inbox"})
    ).json()
    assert len(only_inbox) == 4

    # "Hide service numbers" removes 10086, 95566 and 106*; the mobile, in
    # both stored spellings, is the only conversation left.
    no_service = (
        await phone_client.get(
            "/api/v1/phone/messages", params={"hide_service_numbers": "true"}
        )
    ).json()
    assert [row["body"] for row in no_service] == [
        "reply to the mobile",
        "received from the mobile",
    ]
    assert {row["address"] for row in no_service} == {
        "13800138000",
        "+8613800138000",
    }

    # Several explicit numbers hide together (the multi-select).
    without_two = (
        await phone_client.get(
            "/api/v1/phone/messages",
            params=[("exclude_addresses", "10086"), ("exclude_addresses", "95566")],
        )
    ).json()
    assert [row["body"] for row in without_two] == [
        "bulk promotion",
        "reply to the mobile",
        "received from the mobile",
    ]

    # A full number is matched anywhere, so both stored spellings disappear.
    without_mobile = (
        await phone_client.get(
            "/api/v1/phone/messages",
            params={"exclude_addresses": "+8613800138000"},
        )
    ).json()
    assert "+8613800138000" not in {row["address"] for row in without_mobile}
    assert "13800138000" not in {row["address"] for row in without_mobile}
    assert len(without_mobile) == 4

    # A short fragment is a prefix: hiding 106 must not hide a mobile that
    # happens to contain those digits somewhere in the middle.
    no_prefix = (
        await phone_client.get(
            "/api/v1/phone/messages", params={"exclude_addresses": "106"}
        )
    ).json()
    assert [row["body"] for row in no_prefix] == [
        "bank notice",
        "carrier confirmation",
        "carrier notice",
        "reply to the mobile",
        "received from the mobile",
    ]


@pytest.mark.asyncio
async def test_message_service_and_origin_filters(
    phone_client: httpx.AsyncClient,
) -> None:
    """Non-user numbers are one rule, and international is its own view."""
    device = await register(phone_client)
    device_id = device["id"]
    shapes = [
        ("401", "mobile inbox", "+8613800138000", "inbox", 50),
        ("402", "mobile sent", "13800138000", "sent", 45),
        ("403", "landline", "01012345678", "inbox", 40),
        ("404", "us number", "+14155552671", "inbox", 35),
        ("405", "uk number", "00447911123456", "inbox", 30),
        ("406", "118114 extension", "1181142200013", "inbox", 35),
        ("407", "12520 extension", "125200201211002424", "inbox", 30),
        ("408", "carrier extension", "10001885", "inbox", 25),
        ("409", "bulk sender", "1069000000001", "inbox", 20),
        ("410", "short code", "10086", "inbox", 15),
        ("411", "106 with plus", "+1069385100336405", "inbox", 10),
        ("412", "long international", "00791345367763021483", "inbox", 5),
        ("413", "+00 malformed", "+006287743828294", "inbox", 1),
    ]
    for batch in (shapes[:5], shapes[5:10], shapes[10:]):
        response = await sync(
            phone_client,
            device_id,
            "message",
            [
                message(
                    external_id,
                    body,
                    address=address,
                    direction=direction,
                    received_at=iso(minutes=minutes),
                )
                for external_id, body, address, direction, minutes in batch
            ],
        )
        assert response.status_code == 200, response.text

    hidden = (
        await phone_client.get(
            "/api/v1/phone/messages",
            params={"hide_service_numbers": "true", "limit": 50},
        )
    ).json()
    assert [row["body"] for row in hidden] == [
        "uk number",
        "us number",
        "landline",
        "mobile sent",
        "mobile inbox",
    ]
    assert "1181142200013" not in {row["address"] for row in hidden}
    assert "125200201211002424" not in {row["address"] for row in hidden}
    assert "10001885" not in {row["address"] for row in hidden}
    # A bulk sender or a malformed number with an international-looking
    # prefix is not an international user; E.164 bounds the international view.
    assert "+1069385100336405" not in {row["address"] for row in hidden}
    assert "006287743828294" not in {row["address"] for row in hidden}
    assert "00791345367763021483" not in {row["address"] for row in hidden}

    international = (
        await phone_client.get(
            "/api/v1/phone/messages",
            params={"origin": "international", "limit": 50},
        )
    ).json()
    assert [row["address"] for row in international] == [
        "00447911123456",
        "+14155552671",
    ]

    domestic = (
        await phone_client.get(
            "/api/v1/phone/messages", params={"origin": "domestic", "limit": 50}
        )
    ).json()
    assert [row["body"] for row in domestic] == [
        "+00 malformed",
        "long international",
        "106 with plus",
        "short code",
        "bulk sender",
        "carrier extension",
        "12520 extension",
        "118114 extension",
        "landline",
        "mobile sent",
        "mobile inbox",
    ]

    normal_domestic = (
        await phone_client.get(
            "/api/v1/phone/messages",
            params={
                "origin": "domestic",
                "hide_service_numbers": "true",
                "limit": 50,
            },
        )
    ).json()
    assert [row["body"] for row in normal_domestic] == [
        "landline",
        "mobile sent",
        "mobile inbox",
    ]

    # The picker follows the same filters, so it does not offer a sender that
    # the list already hides.
    normal_senders = (
        await phone_client.get(
            "/api/v1/phone/messages/senders",
            params={"hide_service_numbers": "true", "limit": 20},
        )
    ).json()
    assert {row["address"] for row in normal_senders} == {
        "13800138000",
        "01012345678",
        "14155552671",
        "00447911123456",
    }

    international_senders = (
        await phone_client.get(
            "/api/v1/phone/messages/senders",
            params={"origin": "international", "limit": 20},
        )
    ).json()
    assert {row["address"] for row in international_senders} == {
        "14155552671",
        "00447911123456",
    }

    domestic_senders = (
        await phone_client.get(
            "/api/v1/phone/messages/senders",
            params={
                "origin": "domestic",
                "hide_service_numbers": "true",
                "limit": 20,
            },
        )
    ).json()
    assert {row["address"] for row in domestic_senders} == {
        "13800138000",
        "01012345678",
    }


@pytest.mark.asyncio
async def test_message_senders_merge_country_code_spellings(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    await seed_known_senders(phone_client, device["id"])

    senders = (await phone_client.get("/api/v1/phone/messages/senders")).json()
    counts = {row["address"]: row["count"] for row in senders}
    assert counts["10086"] == 2
    assert counts["13800138000"] == 2
    assert counts["1069000000001"] == 1
    # One entry for the mobile, not "+8613800138000" next to "13800138000".
    assert "+8613800138000" not in counts
    # Newest last, count first: the carrier wrote twice and most recently of
    # the two-count senders.
    assert senders[0]["address"] == "10086"
    assert {row["address"] for row in senders} == {
        "10086",
        "13800138000",
        "1069000000001",
        "95566",
    }


@pytest.mark.asyncio
async def test_message_thread_is_one_conversation_oldest_first(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    await seed_known_senders(phone_client, device["id"])

    # Opening either spelling returns both messages, in reading order.
    for spelling in ("+8613800138000", "13800138000"):
        thread = (
            await phone_client.get(
                "/api/v1/phone/messages/thread", params={"address": spelling}
            )
        ).json()
        assert [row["body"] for row in thread] == [
            "received from the mobile",
            "reply to the mobile",
        ]
        assert [row["direction"] for row in thread] == ["inbox", "sent"]

    # The thread is scoped to the device that owns the row, like the list.
    other = await register(
        phone_client,
        device_key="phone-key-0002",
        name="Second phone",
        model="Pixel 9",
    )
    await sync(
        phone_client,
        other["id"],
        "message",
        [message("301", "same number elsewhere", address="13800138000")],
    )
    scoped = (
        await phone_client.get(
            "/api/v1/phone/messages/thread",
            params={"address": "13800138000", "device_id": device["id"]},
        )
    ).json()
    assert [row["body"] for row in scoped] == [
        "received from the mobile",
        "reply to the mobile",
    ]


@pytest.mark.asyncio
async def test_message_thread_keeps_an_alphanumeric_sender(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    await sync(
        phone_client,
        device["id"],
        "message",
        [message("321", "your code is 1234", address="BANK")],
    )

    # An alphanumeric sender id is not a phone number: digits alone would let
    # a thread for "BANK" collide with any number containing its digits.
    thread = (
        await phone_client.get(
            "/api/v1/phone/messages/thread", params={"address": "bank"}
        )
    ).json()
    assert [row["body"] for row in thread] == ["your code is 1234"]

    senders = (await phone_client.get("/api/v1/phone/messages/senders")).json()
    assert [row["address"] for row in senders] == ["bank"]

    # Hiding it is case-insensitive and uses the literal id, not its digits.
    hidden = (
        await phone_client.get(
            "/api/v1/phone/messages", params={"exclude_addresses": "bank"}
        )
    ).json()
    assert hidden == []

    kept = (
        await phone_client.get(
            "/api/v1/phone/messages", params={"exclude_addresses": "10086"}
        )
    ).json()
    assert [row["body"] for row in kept] == ["your code is 1234"]


@pytest.mark.asyncio
async def test_full_calendar_snapshot_deletes_missing_events(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    events = [
        {
            "external_id": "501",
            "calendar_name": "Work",
            "title": "Standup",
            "starts_at": iso(hours=2),
            "ends_at": iso(hours=1),
        },
        {
            "external_id": "502",
            "calendar_name": "Work",
            "title": "Dentist",
            "starts_at": iso(hours=5),
            "ends_at": iso(hours=4),
        },
    ]
    first = await sync(
        phone_client, device["id"], "calendar", events, mode="full"
    )
    assert first.json()["accepted"] == 2

    second = await sync(
        phone_client, device["id"], "calendar", events[:1], mode="full"
    )
    assert second.json()["accepted"] == 1
    assert second.json()["deleted"] == 1

    listing = (await phone_client.get("/api/v1/phone/calendar")).json()
    assert [row["title"] for row in listing] == ["Standup"]
    assert listing[0]["id"] == "501"


# A real Huawei/HarmonyOS calendar provider stores TimeZone.toString() in
# Events.EVENT_TIMEZONE instead of an Olson id. The dump is 267 characters.
JAVA_TIMEZONE_DUMP = (
    "java.util.SimpleTimeZone[id=Asia/Shanghai,offset=28800000,dstSavings=0,"
    "useDaylight=false,transitions=0,lastRule=null,startMode=0,startMonth=0,"
    "startDay=0,startDayOfWeek=0,startTime=0,startTimeMode=0,endMode=0,"
    "endMonth=0,endDay=0,endDayOfWeek=0,endTime=0,endTimeMode=0]"
)


@pytest.mark.asyncio
async def test_provider_timezone_dump_is_normalised_not_rejected(
    phone_client: httpx.AsyncClient,
) -> None:
    """Keep the event and reduce the dump to the id it contains.

    This exact value made every calendar batch fail on a real phone, and the
    Android client aborted the whole sync run on that failure, so no kind
    after ``calendar`` was ever uploaded.
    """
    assert len(JAVA_TIMEZONE_DUMP) > 64
    device = await register(phone_client)
    event = {
        "external_id": "601",
        "title": "Dentist",
        "starts_at": iso(hours=2),
        "timezone": JAVA_TIMEZONE_DUMP,
    }
    response = await sync(phone_client, device["id"], "calendar", [event])
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1

    listing = (await phone_client.get("/api/v1/phone/calendar")).json()
    assert listing[0]["timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_a_rejected_item_is_422_not_500(
    phone_client: httpx.AsyncClient,
) -> None:
    """One malformed field names the field instead of an opaque 500.

    The status matters: a 500 tells the phone nothing and reads like a server
    crash, and this handler used to let the ValidationError escape.
    """
    device = await register(phone_client)
    response = await sync(
        phone_client,
        device["id"],
        "calendar",
        [{"external_id": "602", "timezone": "x" * 200}],
    )
    assert response.status_code == 422, response.text
    assert "timezone" in response.json()["detail"]


@pytest.mark.asyncio
async def test_incremental_delete_hides_a_message(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    await sync(phone_client, device["id"], "message", [message("301", "delete me")])
    deleted = await sync(
        phone_client, device["id"], "message", [], deleted=["301"]
    )
    assert deleted.json()["deleted"] == 1
    assert (await phone_client.get("/api/v1/phone/messages")).json() == []


@pytest.mark.asyncio
async def test_search_merges_kinds_by_time(phone_client: httpx.AsyncClient) -> None:
    device = await register(phone_client)
    device_id = device["id"]
    await sync(
        phone_client,
        device_id,
        "message",
        [message("401", "the dentist moved my appointment")],
    )
    await sync(
        phone_client,
        device_id,
        "calendar",
        [
            {
                "external_id": "402",
                "title": "Dentist appointment",
                "starts_at": iso(hours=1),
                "ends_at": iso(minutes=30),
            }
        ],
        mode="full",
    )
    await sync(
        phone_client,
        device_id,
        "call",
        [
            {
                "external_id": "403",
                "number": "+8613900139000",
                "cached_name": "Dentist clinic",
                "direction": "missed",
                "started_at": iso(hours=3),
                "duration_seconds": 0,
            }
        ],
    )
    await sync(
        phone_client,
        device_id,
        "notification",
        [
            {
                "external_id": "404",
                "package_name": "com.example.health",
                "app_label": "Health",
                "title": "Appointment reminder",
                "text": "Dentist tomorrow at 10:00",
                "posted_at": iso(minutes=5),
            }
        ],
    )

    response = await phone_client.get(
        "/api/v1/phone/search", params={"q": "dentist"}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    kinds = [hit["kind"] for hit in payload["hits"]]
    assert kinds == ["notification", "message", "calendar", "call"]
    assert payload["hits"][0]["title"].startswith("Health:")


@pytest.mark.asyncio
async def test_ambient_kinds_round_trip(phone_client: httpx.AsyncClient) -> None:
    device = await register(phone_client)
    device_id = device["id"]

    await sync(
        phone_client,
        device_id,
        "location",
        [
            {
                "external_id": "loc-1",
                "latitude": 31.2304,
                "longitude": 121.4737,
                "accuracy_m": 12.5,
                "provider": "network",
                "captured_at": iso(minutes=1),
            }
        ],
    )
    await sync(
        phone_client,
        device_id,
        "app",
        [
            {
                "external_id": "com.tencent.mm",
                "label": "WeChat",
                "package_name": "com.tencent.mm",
                "version_name": "8.0.50",
                "is_system": False,
                "updated_at": iso(days=10),
            }
        ],
        mode="full",
    )

    locations = (await phone_client.get("/api/v1/phone/locations")).json()
    assert locations[0]["latitude"] == pytest.approx(31.2304)
    assert locations[0]["accuracy_m"] == pytest.approx(12.5)

    apps = (await phone_client.get("/api/v1/phone/apps")).json()
    assert apps[0]["label"] == "WeChat"
    assert apps[0]["package_name"] == "com.tencent.mm"

    notifications = (await phone_client.get("/api/v1/phone/notifications")).json()
    assert notifications == []


@pytest.mark.asyncio
async def test_command_lifecycle(phone_client: httpx.AsyncClient) -> None:
    device = await register(phone_client)
    device_id = device["id"]

    proposed = await phone_client.post(
        "/api/v1/phone/commands",
        json={
            "kind": "calendar.create",
            "payload": {
                "title": "Dentist",
                "starts_at": iso(hours=1),
                "location": "Clinic",
            },
        },
    )
    assert proposed.status_code == 201, proposed.text
    command = proposed.json()
    assert command["status"] == "proposed"
    assert command["summary"].startswith('create calendar event "Dentist"')
    assert command["payload"]["ends_at"]

    # Not approved: the phone must not claim it yet.
    claim = await phone_client.get(
        f"/api/v1/phone/devices/{device_id}/commands"
    )
    assert claim.json() == []

    approved = await phone_client.post(
        f"/api/v1/phone/commands/{command['id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    claim = await phone_client.get(
        f"/api/v1/phone/devices/{device_id}/commands"
    )
    claimed = claim.json()
    assert len(claimed) == 1
    assert claimed[0]["status"] == "dispatched"
    assert claimed[0]["payload"]["title"] == "Dentist"

    # A second poll must not hand the same command out twice.
    assert (
        await phone_client.get(f"/api/v1/phone/devices/{device_id}/commands")
    ).json() == []

    result = await phone_client.post(
        f"/api/v1/phone/devices/{device_id}/commands/{command['id']}/result",
        json={"status": "succeeded", "result": "event 991 created"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "succeeded"
    assert result.json()["result"] == "event 991 created"


@pytest.mark.asyncio
async def test_pending_command_is_not_approved_twice(
    phone_client: httpx.AsyncClient,
) -> None:
    await register(phone_client)
    command = (
        await phone_client.post(
            "/api/v1/phone/commands",
            json={
                "kind": "sms.send",
                "payload": {"address": "+8613800138000", "body": "hello"},
            },
        )
    ).json()
    assert command["status"] == "proposed"

    cancelled = await phone_client.post(
        f"/api/v1/phone/commands/{command['id']}/cancel"
    )
    assert cancelled.json()["status"] == "cancelled"

    rejected = await phone_client.post(
        f"/api/v1/phone/commands/{command['id']}/approve"
    )
    assert rejected.status_code == 400
    assert "cancelled" in rejected.json()["detail"]


@pytest.mark.asyncio
async def test_batch_limit_and_full_mode_guards(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    too_many = await sync(
        phone_client,
        device["id"],
        "message",
        [message(str(1000 + index), "x") for index in range(6)],
    )
    assert too_many.status_code == 400
    assert "limit is 5" in too_many.json()["detail"]

    full_messages = await sync(
        phone_client, device["id"], "message", [message("1", "x")], mode="full"
    )
    assert full_messages.status_code == 400
    assert "full snapshot is not supported" in full_messages.json()["detail"]


@pytest.mark.asyncio
async def test_long_text_is_truncated(phone_client: httpx.AsyncClient) -> None:
    device = await register(phone_client)
    await sync(
        phone_client,
        device["id"],
        "message",
        [message("601", "x" * 500)],
    )
    listing = (await phone_client.get("/api/v1/phone/messages")).json()
    assert len(listing[0]["body"]) == 200


@pytest.mark.asyncio
async def test_retention_removes_old_messages(tmp_path: Path) -> None:
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        phone=PhoneConfig(
            enabled=True,
            retention=PhoneRetentionConfig(message_days=1),
        ),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'retention.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            device = await register(client)
            await sync(
                client,
                device["id"],
                "message",
                [
                    message("701", "old", received_at=iso(days=10)),
                    message("702", "new"),
                ],
            )
            listing = (await client.get("/api/v1/phone/messages")).json()
            assert [row["body"] for row in listing] == ["new"]
    await http.aclose()


@pytest.mark.asyncio
async def test_wipe_device_keeps_registration(phone_client: httpx.AsyncClient) -> None:
    device = await register(phone_client)
    await sync(phone_client, device["id"], "message", [message("801", "gone soon")])

    wiped = await phone_client.delete(f"/api/v1/phone/devices/{device['id']}/data")
    assert wiped.status_code == 200
    assert wiped.json()["removed"] >= 1

    assert (await phone_client.get("/api/v1/phone/messages")).json() == []
    devices = (await phone_client.get("/api/v1/phone/devices")).json()
    assert len(devices) == 1
    assert devices[0]["states"] == []


@pytest.mark.asyncio
async def test_device_snapshot_updates_registration(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    response = await sync(
        phone_client,
        device["id"],
        "device",
        [],
        mode="full",
        device={
            "name": "Pixel 8 Pro",
            "model": "Pixel 8 Pro",
            "os_version": "17",
            "app_version": "0.2.0",
            "timezone": "Asia/Shanghai",
            "scopes": ["message"],
            "info": {"battery_percent": 42},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1

    refreshed = (await phone_client.get(f"/api/v1/phone/devices/{device['id']}")).json()
    assert refreshed["name"] == "Pixel 8 Pro"
    assert refreshed["app_version"] == "0.2.0"
    assert refreshed["scopes"] == ["message"]
    assert refreshed["info"]["battery_percent"] == 42
    assert refreshed["info"]["network"] == "wifi"


@pytest.mark.asyncio
async def test_devices_are_isolated_between_accounts(tmp_path: Path) -> None:
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        auth=AuthConfig(enabled=True, allow_registration=True),
        phone=PhoneConfig(enabled=True),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'accounts.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            tokens = {}
            for name in ("alice", "bob"):
                response = await client.post(
                    "/api/v1/auth/register",
                    json={"username": name, "password": "password123"},
                )
                assert response.status_code == 201, response.text
                tokens[name] = response.json()["access_token"]

            alice = {"Authorization": f"Bearer {tokens['alice']}"}
            bob = {"Authorization": f"Bearer {tokens['bob']}"}

            device = await register(client, headers=alice)
            await client.post(
                f"/api/v1/phone/devices/{device['id']}/sync",
                json={"kind": "message", "items": [message("901", "private")]},
                headers=alice,
            )

            assert (await client.get("/api/v1/phone/messages", headers=alice)).json()
            assert (
                await client.get("/api/v1/phone/messages", headers=bob)
            ).json() == []
            assert (
                await client.get("/api/v1/phone/devices", headers=bob)
            ).json() == []
            assert (
                await client.get(
                    f"/api/v1/phone/devices/{device['id']}", headers=bob
                )
            ).status_code == 404
            assert (
                await client.post(
                    f"/api/v1/phone/devices/{device['id']}/sync",
                    json={"kind": "message", "items": []},
                    headers=bob,
                )
            ).status_code == 404
            assert (
                await client.delete(
                    f"/api/v1/phone/devices/{device['id']}/data", headers=bob
                )
            ).status_code == 404
    await http.aclose()


@pytest.mark.asyncio
async def test_expired_proposal_cannot_be_approved(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'clock.db'}", echo=False
    )
    await create_all(engine)
    session_factory = create_session_factory(engine)
    current = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    service = PhoneService(
        PhoneConfig(enabled=True, command_ttl_seconds=60),
        session_factory=session_factory,
        clock=lambda: current,
    )
    try:
        device = await service.register_device(
            "local",
            DeviceRegister(device_key="clock-device-1", name="Clock"),
        )
        command = await service.propose_command(
            "local",
            "sms.send",
            {"address": "+8613800138000", "body": "later"},
        )
        service.clock = lambda: current + timedelta(seconds=120)
        with pytest.raises(InvalidCommand, match="expired"):
            await service.approve_command("local", command["id"])
        pending = await service.pending_commands(
            "local", device["id"]
        )
        assert pending == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_kind_and_device(phone_client: httpx.AsyncClient) -> None:
    missing = uuid4()
    response = await phone_client.post(
        f"/api/v1/phone/devices/{missing}/sync",
        json={"kind": "message", "items": []},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_calendar_window_snapshot_keeps_events_outside_it(
    phone_client: httpx.AsyncClient,
) -> None:
    """A windowed full snapshot must not delete history outside the window."""
    device = await register(phone_client)
    old = {
        "external_id": "old-1",
        "title": "Last year",
        "starts_at": (NOW - timedelta(days=400)).isoformat(),
        "ends_at": (NOW - timedelta(days=400) + timedelta(hours=1)).isoformat(),
    }
    await sync(phone_client, device["id"], "calendar", [old])

    inside = {
        "external_id": "new-1",
        "title": "This week",
        "starts_at": (NOW + timedelta(hours=1)).isoformat(),
        "ends_at": (NOW + timedelta(hours=2)).isoformat(),
    }
    result = await sync(
        phone_client,
        device["id"],
        "calendar",
        [inside],
        mode="full",
        since=NOW - timedelta(days=30),
        until=NOW + timedelta(days=30),
    )
    assert result.status_code == 200, result.text
    assert result.json()["deleted"] == 0

    titles = sorted(
        row["title"]
        for row in (await phone_client.get("/api/v1/phone/calendar")).json()
    )
    assert titles == ["Last year", "This week"]

    # A second windowed snapshot that omits the in-window event deletes it and
    # still leaves the out-of-window one alone.
    removed = await sync(
        phone_client,
        device["id"],
        "calendar",
        [],
        mode="full",
        since=NOW - timedelta(days=30),
        until=NOW + timedelta(days=30),
    )
    assert removed.json()["deleted"] == 1
    titles = [
        row["title"]
        for row in (await phone_client.get("/api/v1/phone/calendar")).json()
    ]
    assert titles == ["Last year"]


@pytest.mark.asyncio
async def test_revoked_device_is_blocked_until_restored(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)

    assert (
        await phone_client.delete(f"/api/v1/phone/devices/{device['id']}")
    ).status_code == 204

    blocked = await sync(
        phone_client, device["id"], "message", [message("revoked-1", "no")]
    )
    assert blocked.status_code == 403
    assert "revoked" in blocked.json()["detail"]

    # Re-registering the same device key must not silently undo the revocation.
    re_registered = await phone_client.post(
        "/api/v1/phone/devices", json=DEVICE
    )
    assert re_registered.status_code == 403

    restored = await phone_client.post(
        f"/api/v1/phone/devices/{device['id']}/restore"
    )
    assert restored.status_code == 200
    assert restored.json()["revoked"] is False

    accepted = await sync(
        phone_client, device["id"], "message", [message("revoked-2", "yes")]
    )
    assert accepted.status_code == 200, accepted.text


@pytest.mark.asyncio
async def test_search_keeps_locations_out_of_text_matches(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    await sync(
        phone_client,
        device["id"],
        "location",
        [
            {
                "external_id": "loc-search-1",
                "latitude": 31.2304,
                "longitude": 121.4737,
                "accuracy_m": 10.0,
                "provider": "network",
                "captured_at": iso(minutes=3),
            }
        ],
    )
    await sync(
        phone_client,
        device["id"],
        "message",
        [message("6101", "the dentist called")],
    )

    unrelated = (
        await phone_client.get(
            "/api/v1/phone/search", params={"q": "dentist"}
        )
    ).json()
    assert [hit["kind"] for hit in unrelated["hits"]] == ["message"]

    by_coordinates = (
        await phone_client.get(
            "/api/v1/phone/search", params={"q": "31.2304"}
        )
    ).json()
    assert [hit["kind"] for hit in by_coordinates["hits"]] == ["location"]


@pytest.mark.asyncio
async def test_calendar_read_expands_a_lunar_birthday(
    phone_client: httpx.AsyncClient,
) -> None:
    """The read path, not just the recurrence helper, must resolve the lun date.

    The row is the owner's real one: a yearly rule whose BYMONTH/BYMONTHDAY are
    lunar (冬月十二), with a Gregorian DTSTART for 2024. Read as Gregorian it
    would be 2026-11-12; the phone shows it on 2026-12-20.
    """
    device = await register(phone_client)
    result = await sync(
        phone_client,
        device["id"],
        "calendar",
        [
            {
                "external_id": "lunar-1",
                "title": "圆圆生日",
                "starts_at": "2024-12-12T00:00:00+00:00",
                "ends_at": "2024-12-13T00:00:00+00:00",
                "all_day": True,
                "timezone": "UTC",
                "recurrence": "FREQ=YEARLY;INTERVAL=1;WKST=MO;BYMONTHDAY=12;BYMONTH=11",
            }
        ],
    )
    assert result.status_code == 200, result.text

    rows = (
        await phone_client.get(
            "/api/v1/phone/calendar",
            params={
                "since": "2026-11-01T00:00:00Z",
                "until": "2027-01-01T00:00:00Z",
            },
        )
    ).json()

    starts = [datetime.fromisoformat(row["starts_at"]) for row in rows]
    assert starts == [
        datetime(2026, 12, 20, tzinfo=timezone.utc)
    ], rows


@pytest.mark.asyncio
async def test_contact_birthdays_round_trip(phone_client: httpx.AsyncClient) -> None:
    """A birthday stored on a contact reaches the server and comes back.

    Android's Event row is `YYYY-MM-DD` or `--MM-DD`; the client splits it, and
    the year stays optional because the provider's year is optional.
    """
    device = await register(phone_client)
    result = await sync(
        phone_client,
        device["id"],
        "contact",
        [
            {
                "external_id": "c-1",
                "display_name": "圆圆",
                "numbers": ["13800000000"],
                "birthdays": [
                    {"month": 12, "day": 12, "year": 2024, "type": "birthday"},
                    {"month": 5, "day": 20, "type": "anniversary"},
                ],
            }
        ],
    )
    assert result.status_code == 200, result.text

    rows = (await phone_client.get("/api/v1/phone/contacts")).json()
    assert rows[0]["birthdays"] == [
        {"month": 12, "day": 12, "year": 2024, "type": "birthday"},
        {"month": 5, "day": 20, "year": None, "type": "anniversary"},
    ]


@pytest.mark.asyncio
async def test_a_contact_birthday_that_is_not_a_date_is_rejected(
    phone_client: httpx.AsyncClient,
) -> None:
    device = await register(phone_client)
    result = await sync(
        phone_client,
        device["id"],
        "contact",
        [
            {
                "external_id": "c-1",
                "display_name": "X",
                "birthdays": [{"month": 2, "day": 30}],
            }
        ],
    )

    assert result.status_code == 422
    assert "birthday" in result.text
