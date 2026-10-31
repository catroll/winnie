"""Push delivery: REST backlog, live WebSocket, expiry, targeting and events."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from winnie_api.main import create_app
from winnie_api.push.events import PhoneEventsAdapter
from winnie_api.settings import DatabaseConfig, Settings
from winnie_core.config import AuthConfig, JobsConfig
from winnie_core.db import create_all, create_engine, create_session_factory
from winnie_phone.config import PhoneConfig
from winnie_phone.schemas import DeviceRegister
from winnie_phone.service import PhoneService
from winnie_push.config import PushConfig
from winnie_push.service import PushService


def app_for(settings: Settings) -> TestClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    return TestClient(app)


@pytest.mark.asyncio
async def test_publish_and_read_the_backlog(push_client: httpx.AsyncClient) -> None:
    published = await push_client.post(
        "/api/v1/push/test", json={"title": "Hello", "body": "World"}
    )
    assert published.status_code == 201, published.text
    event = published.json()
    assert event["title"] == "Hello"
    assert event["body"] == "World"
    assert event["kind"] == "notification"
    assert event["seq"] > 0

    listing = (await push_client.get("/api/v1/push/notifications")).json()
    assert [row["title"] for row in listing["events"]] == ["Hello"]
    assert listing["last_seq"] == event["seq"]

    newer = (
        await push_client.get(
            "/api/v1/push/notifications", params={"after_seq": event["seq"]}
        )
    ).json()
    assert newer["events"] == []
    assert newer["last_seq"] == event["seq"]

    status = (await push_client.get("/api/v1/push/status")).json()
    assert status["pending"] >= 1
    assert status["connected"] == 0


def test_websocket_sends_backlog_then_live_events(push_settings: Settings) -> None:
    client = app_for(push_settings)
    try:
        with client:
            # A reminder written while the phone was offline.
            backlog = client.post(
                "/api/v1/push/test", json={"title": "Offline", "body": "queued"}
            ).json()

            with client.websocket_connect("/api/v1/push/ws?after=0") as socket:
                first = socket.receive_json()
                assert first["type"] == "event"
                assert first["title"] == "Offline"
                assert first["seq"] == backlog["seq"]
                ready = socket.receive_json()
                assert ready["type"] == "ready"

                # Live: the socket is connected, so the same publish arrives
                # without a reconnect.
                client.post(
                    "/api/v1/push/test", json={"title": "Live", "body": "now"}
                )
                live = socket.receive_json()
                assert live["type"] == "event"
                assert live["title"] == "Live"
                assert live["seq"] > backlog["seq"]
    finally:
        client.close()


def test_websocket_requires_a_token_when_auth_is_enabled(tmp_path: Path) -> None:
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        auth=AuthConfig(enabled=True, allow_registration=True),
        push=PushConfig(enabled=True, heartbeat_seconds=5),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'push-auth.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )
    client = app_for(settings)
    try:
        with client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/api/v1/push/ws"):
                    pass

            assert client.get("/api/v1/push/notifications").status_code == 401

            registered = client.post(
                "/api/v1/auth/register",
                json={"username": "alice", "password": "password123"},
            )
            assert registered.status_code == 201, registered.text
            token = registered.json()["access_token"]

            with client.websocket_connect(
                f"/api/v1/push/ws?token={token}"
            ) as socket:
                assert socket.receive_json()["type"] == "ready"
    finally:
        client.close()


def _engine(tmp_path: Path):
    return create_engine(f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")


@pytest.mark.asyncio
async def test_sync_events_are_device_targeted(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    await create_all(engine)
    factory = create_session_factory(engine)
    service = PushService(
        PushConfig(enabled=True), session_factory=factory
    )
    try:
        device_a = uuid4()
        device_b = uuid4()
        await service.publish(
            "local",
            kind="sync",
            title="",
            body="",
            device_id=device_a,
            ttl_seconds=300,
        )
        await service.publish("local", title="Broadcast")

        assert [
            event["kind"]
            for event in await service.backlog("local", device_id=device_a)
        ] == ["sync", "notification"]
        assert [
            event["kind"]
            for event in await service.backlog("local", device_id=device_b)
        ] == ["notification"]
        # A client without a device registration only sees broadcasts.
        assert [
            event["kind"] for event in await service.backlog("local")
        ] == ["notification"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_events_are_skipped_and_pruned(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    await create_all(engine)
    factory = create_session_factory(engine)
    current = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    service = PushService(
        PushConfig(enabled=True, retention_days=7),
        session_factory=factory,
        clock=lambda: current,
    )
    try:
        await service.publish("local", title="Short", ttl_seconds=60)
        service.clock = lambda: current + timedelta(seconds=120)
        assert await service.backlog("local") == []

        assert await service.prune("local") == 1
        assert await service.backlog("local") == []

        service.clock = lambda: current + timedelta(days=30)
        await service.publish("local", title="Old")
        service.clock = lambda: current + timedelta(days=60)
        assert await service.prune("local") == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_phone_command_publishes_sync_then_result(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    await create_all(engine)
    factory = create_session_factory(engine)
    push = PushService(PushConfig(enabled=True), session_factory=factory)
    phone = PhoneService(
        PhoneConfig(enabled=True),
        session_factory=factory,
        events=PhoneEventsAdapter(push),
    )
    try:
        device = await phone.register_device(
            "local",
            DeviceRegister(device_key="push-device-1", name="Pixel"),
        )
        device_id = UUID(str(device["id"]))
        command = await phone.propose_command(
            "local",
            "sms.send",
            {"address": "+8613800138000", "body": "hello"},
        )

        await phone.approve_command("local", UUID(str(command["id"])))
        synced = await push.backlog("local", device_id=device_id)
        assert len(synced) == 1
        assert synced[0]["kind"] == "sync"
        assert synced[0]["device_id"] == str(device_id)
        assert synced[0]["data"]["command_id"] == str(command["id"])

        await phone.pending_commands("local", device_id)
        await phone.complete_command(
            "local",
            UUID(str(command["id"])),
            status="succeeded",
            result="submitted",
            device_id=device_id,
        )
        events = await push.backlog("local", device_id=device_id)
        assert len(events) == 2
        result = events[1]
        assert result["kind"] == "phone_command"
        assert result["title"] == "Phone action completed"
        assert "send SMS" in result["body"]
        assert result["data"]["status"] == "succeeded"
    finally:
        await engine.dispose()
