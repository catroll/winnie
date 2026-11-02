"""Volcengine Doubao streaming ASR adapter (SAUC 2.0).

The endpoint is the bidirectional streaming WebSocket, not the recording-file
HTTP API used by ``volcengine.py``. Audio is raw 16 kHz mono signed 16-bit PCM;
the adapter gzips each 100 ms frame and forwards it unchanged in time order.

Frames use the vendor's binary envelope:

    header (4 bytes) | [sequence] | payload size (4 bytes) | payload

The first request is a gzipped JSON full-client request with sequence 1. Each
audio frame is a gzipped audio-only request. The final frame sets the "last
packet" flag. Server responses are full-server JSON frames; a non-success
error frame raises :class:`RealtimeASRError`.

Reference: https://www.volcengine.com/docs/6561/1354869
"""

from __future__ import annotations

import asyncio
import gzip
import json
import struct
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import websockets

from winnie_llm.asr.realtime import (
    RealtimeASRError,
    RealtimeEvent,
    StreamStarted,
    TranscriptUpdate,
)

DEFAULT_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
DEFAULT_RESOURCE_ID = "volc.seedasr.sauc.duration"

_PROTOCOL_VERSION = 0b0001
_HEADER_SIZE = 0b0001
_FULL_CLIENT_REQUEST = 0b0001
_AUDIO_ONLY_REQUEST = 0b0010
_FULL_SERVER_RESPONSE = 0b1001
_SERVER_ERROR_RESPONSE = 0b1111
_NO_SEQUENCE = 0b0000
_POSITIVE_SEQUENCE = 0b0001
_LAST_PACKET = 0b0010
_JSON_SERIALIZATION = 0b0001
_RAW_SERIALIZATION = 0b0000
_GZIP_COMPRESSION = 0b0001


@dataclass(frozen=True)
class _Frame:
    text: str | None
    final: bool


class VolcengineRealtimeASR:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        resource_id: str = DEFAULT_RESOURCE_ID,
        model_name: str = "bigmodel",
        open_timeout_seconds: float = 15.0,
        recv_timeout_seconds: float = 60.0,
        max_message_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self.api_key = api_key
        self.endpoint = endpoint
        self.resource_id = resource_id
        self.model_name = model_name
        self.open_timeout_seconds = open_timeout_seconds
        self.recv_timeout_seconds = recv_timeout_seconds
        self.max_message_bytes = max_message_bytes

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language_hint: str | None = None,
    ) -> AsyncIterator[RealtimeEvent]:
        if not self.api_key:
            raise RealtimeASRError(
                "Volcengine streaming ASR key is missing; set it for the "
                "supplier in Admin -> AI suppliers"
            )

        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        request = _full_client_request(
            model_name=self.model_name,
            language_hint=language_hint,
        )

        try:
            async with websockets.connect(
                self.endpoint,
                additional_headers=headers,
                open_timeout=self.open_timeout_seconds,
                close_timeout=5.0,
                max_size=self.max_message_bytes,
            ) as socket:
                await socket.send(request)
                first = await asyncio.wait_for(
                    socket.recv(), timeout=self.open_timeout_seconds
                )
                _parse_frame(first)  # raises on an error frame
                yield StreamStarted()

                send_task = asyncio.create_task(_send_audio(socket, audio))
                last_text = ""
                try:
                    while True:
                        raw = await asyncio.wait_for(
                            socket.recv(), timeout=self.recv_timeout_seconds
                        )
                        frame = _parse_frame(raw)
                        if frame.text is not None and frame.text != last_text:
                            last_text = frame.text
                            yield TranscriptUpdate(text=last_text, final=frame.final)
                        elif frame.final and last_text:
                            yield TranscriptUpdate(text=last_text, final=True)
                        if frame.final:
                            break
                finally:
                    send_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await send_task
        except RealtimeASRError:
            raise
        except asyncio.TimeoutError as exc:
            raise RealtimeASRError(
                "Volcengine streaming ASR timed out waiting for a response"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - vendor/transport failures
            raise RealtimeASRError(
                f"Volcengine streaming ASR failed: {exc}"
            ) from exc


async def _send_audio(socket: Any, audio: AsyncIterator[bytes]) -> None:
    async for chunk in audio:
        await socket.send(_audio_packet(chunk, last=False))
    await socket.send(_audio_packet(b"", last=True))


def _header(message_type: int, flags: int, *, serialization: int) -> bytes:
    return bytes(
        [
            (_PROTOCOL_VERSION << 4) | _HEADER_SIZE,
            (message_type << 4) | flags,
            (serialization << 4) | _GZIP_COMPRESSION,
            0x00,
        ]
    )


def _full_client_request(*, model_name: str, language_hint: str | None) -> bytes:
    audio: dict[str, object] = {
        "format": "pcm",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
        "codec": "raw",
    }
    if language_hint:
        audio["language"] = _language_code(language_hint)
    payload = gzip.compress(
        json.dumps(
            {
                "user": {"uid": "winnie"},
                "audio": audio,
                "request": {
                    "model_name": model_name,
                    "enable_itn": True,
                    "enable_punc": True,
                    "show_utterances": True,
                    # Cumulative text keeps the client's last caption in one
                    # field; the file pass produces the canonical transcript.
                    "result_type": "full",
                    # Return a definite utterance after a short silence instead
                    # of waiting for the provider's longer semantic segment.
                    "end_window_size": 800,
                    "force_to_speech_time": 1000,
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return (
        _header(
            _FULL_CLIENT_REQUEST,
            _POSITIVE_SEQUENCE,
            serialization=_JSON_SERIALIZATION,
        )
        + struct.pack(">i", 1)
        + struct.pack(">I", len(payload))
        + payload
    )


def _audio_packet(chunk: bytes, *, last: bool) -> bytes:
    payload = gzip.compress(chunk)
    return (
        _header(
            _AUDIO_ONLY_REQUEST,
            _LAST_PACKET if last else _NO_SEQUENCE,
            serialization=_RAW_SERIALIZATION,
        )
        + struct.pack(">I", len(payload))
        + payload
    )


def _parse_frame(raw: bytes | str) -> _Frame:
    if isinstance(raw, str):
        raise RealtimeASRError("Volcengine streaming ASR returned a text frame")
    if len(raw) < 4:
        raise RealtimeASRError("Volcengine streaming ASR returned a short frame")

    header_size = (raw[0] & 0x0F) * 4
    message_type = raw[1] >> 4
    flags = raw[1] & 0x0F
    compression = raw[2] & 0x0F
    payload = raw[header_size:]
    if flags & 0x01:
        if len(payload) < 4:
            raise RealtimeASRError("Volcengine streaming ASR frame has no sequence")
        payload = payload[4:]

    if message_type == _FULL_SERVER_RESPONSE:
        if len(payload) < 4:
            raise RealtimeASRError("Volcengine streaming ASR frame has no size")
        size = struct.unpack(">I", payload[:4])[0]
        body = payload[4 : 4 + size]
        if compression == _GZIP_COMPRESSION and body:
            try:
                body = gzip.decompress(body)
            except OSError as exc:
                raise RealtimeASRError(
                    "Volcengine streaming ASR sent invalid gzip"
                ) from exc
        try:
            decoded = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RealtimeASRError(
                "Volcengine streaming ASR sent invalid JSON"
            ) from exc
        result = decoded.get("result") or {}
        text = str(result.get("text") or "").strip()
        return _Frame(text=text or None, final=bool(flags & _LAST_PACKET))

    if message_type == _SERVER_ERROR_RESPONSE:
        if len(payload) < 8:
            raise RealtimeASRError("Volcengine streaming ASR returned a short error")
        code = struct.unpack(">I", payload[:4])[0]
        size = struct.unpack(">I", payload[4:8])[0]
        body = payload[8 : 8 + size]
        if compression == _GZIP_COMPRESSION and body:
            body = gzip.decompress(body)
        message = body.decode("utf-8", errors="replace")[:300]
        raise RealtimeASRError(
            f"Volcengine streaming ASR error {code}: {message or 'no message'}"
        )

    # Unknown message kinds are transport noise; keep reading.
    return _Frame(text=None, final=False)


def _language_code(language_hint: str) -> str:
    mapping = {
        "zh": "zh-CN",
        "en": "en-US",
        "ja": "ja-JP",
        "ko": "ko-KR",
        "yue": "yue-CN",
    }
    return mapping.get(language_hint, language_hint)
