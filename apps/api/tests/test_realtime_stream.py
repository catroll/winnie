from __future__ import annotations

import gzip
import json
import struct

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from winnie_api.main import create_app
from winnie_api.settings import Settings
from winnie_meetings.integrations.asr.fake import FakeRealtimeASR
from winnie_meetings.integrations.asr.realtime import (
    RealtimeASRError,
    StreamStarted,
    TranscriptUpdate,
)
from winnie_meetings.integrations.asr.volcengine_stream import (
    _audio_packet,
    _full_client_request,
    _parse_frame,
)


def _server_frame(payload: dict, *, sequence: int = 2, final: bool = False) -> bytes:
    body = gzip.compress(json.dumps(payload).encode("utf-8"))
    flags = 0x03 if final else 0x01
    header = bytes([0x11, (0x09 << 4) | flags, 0x11, 0x00])
    return header + struct.pack(">i", sequence) + struct.pack(">I", len(body)) + body


def _error_frame(code: int, message: str) -> bytes:
    body = gzip.compress(message.encode("utf-8"))
    header = bytes([0x11, (0x0F << 4), 0x11, 0x00])
    return header + struct.pack(">I", code) + struct.pack(">I", len(body)) + body


def test_full_client_request_is_gzipped_json() -> None:
    packet = _full_client_request(model_name="bigmodel", language_hint="zh")
    assert packet[0] == 0x11
    assert packet[1] == 0x11
    assert packet[2] == 0x11
    assert struct.unpack(">i", packet[4:8])[0] == 1
    size = struct.unpack(">I", packet[8:12])[0]
    body = json.loads(gzip.decompress(packet[12 : 12 + size]))
    assert body["audio"] == {
        "format": "pcm",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
        "codec": "raw",
        "language": "zh-CN",
    }
    assert body["request"]["result_type"] == "full"
    assert body["request"]["end_window_size"] == 800


def test_audio_packet_sets_last_flag_and_gzips() -> None:
    normal = _audio_packet(b"abc", last=False)
    assert normal[:4] == bytes([0x11, 0x20, 0x01, 0x00])
    assert gzip.decompress(normal[8:]) == b"abc"

    final = _audio_packet(b"", last=True)
    assert final[:4] == bytes([0x11, 0x22, 0x01, 0x00])


def test_parse_server_frame_extracts_cumulative_text() -> None:
    frame = _parse_frame(
        _server_frame(
            {"result": {"text": "你好", "utterances": [{"text": "你好"}]}},
            sequence=7,
        )
    )
    assert frame.text == "你好"
    assert frame.final is False

    final = _parse_frame(
        _server_frame({"result": {"text": "你好。"}}, sequence=8, final=True)
    )
    assert final.text == "你好。"
    assert final.final is True


def test_parse_error_frame_raises() -> None:
    with pytest.raises(RealtimeASRError, match="45000081"):
        _parse_frame(_error_frame(45000081, "packet wait timeout"))


async def test_fake_realtime_streams_partial_and_final() -> None:
    async def audio():
        yield b"\x00" * 320
        yield b"\x00" * 320

    events = [event async for event in FakeRealtimeASR().stream(audio())]
    assert isinstance(events[0], StreamStarted)
    assert isinstance(events[1], TranscriptUpdate)
    assert events[1].final is False
    assert events[-1].text == "实时字幕，共 2 段。"
    assert events[-1].final is True


def test_websocket_streams_captions(meeting_settings: Settings) -> None:
    app = create_app(settings=meeting_settings)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/meetings",
            json={"title": "Live sync", "source": "realtime"},
        )
        assert created.status_code == 201, created.text
        meeting_id = created.json()["id"]
        assert created.json()["source"] == "realtime"

        with client.websocket_connect(
            f"/api/v1/meetings/{meeting_id}/stream"
        ) as websocket:
            assert websocket.receive_json() == {"type": "ready"}
            websocket.send_bytes(b"\x00" * 3200)
            partial = websocket.receive_json()
            assert partial == {"type": "partial", "text": "实时字幕"}
            websocket.send_json({"type": "stop"})
            final = websocket.receive_json()
            assert final == {"type": "final", "text": "实时字幕，共 1 段。"}


def test_websocket_rejects_unknown_meeting(meeting_settings: Settings) -> None:
    app = create_app(settings=meeting_settings)
    with TestClient(app) as client:
        with pytest.raises(Exception) as excinfo:
            with client.websocket_connect(
                "/api/v1/meetings/00000000-0000-0000-0000-000000000001/stream"
            ) as websocket:
                websocket.receive_json()
        assert excinfo.value.code == 4404


def test_websocket_requires_the_meeting_owner(account_settings: Settings) -> None:
    app = create_app(settings=account_settings)
    with TestClient(app) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "live-owner", "password": "live-password"},
        )
        assert registered.status_code == 201, registered.text
        token = registered.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/meetings",
            headers=headers,
            json={"title": "Owned stream", "source": "realtime"},
        )
        assert created.status_code == 201, created.text
        path = f"/api/v1/meetings/{created.json()['id']}/stream"

        with pytest.raises(WebSocketDisconnect) as missing:
            with client.websocket_connect(path):
                pass
        assert missing.value.code == 4401

        with client.websocket_connect(path, headers=headers) as websocket:
            assert websocket.receive_json() == {"type": "ready"}
