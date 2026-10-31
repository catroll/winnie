from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from winnie_llm import VolcengineASRProvider
from winnie_core.types import AudioRef


async def test_volcengine_submits_polls_and_parses(tmp_path: Path) -> None:
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"fake wave bytes")
    calls = {"query": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/submit"):
            payload = json.loads(request.content)
            assert request.headers["X-Api-Key"] == "api-key"
            assert "X-Api-App-Key" not in request.headers
            assert "X-Api-Access-Key" not in request.headers
            assert request.headers["X-Api-Resource-Id"] == "volc.seedasr.auc"
            assert request.headers["X-Api-Sequence"] == "-1"
            assert payload["audio"]["format"] == "wav"
            assert payload["audio"]["data"] == base64.b64encode(
                audio.read_bytes()
            ).decode("ascii")
            assert payload["request"]["show_utterances"] is True
            return httpx.Response(
                200,
                headers={
                    "X-Api-Status-Code": "20000000",
                    "X-Api-Message": "ok",
                },
            )

        assert request.url.path.endswith("/query")
        assert request.headers["X-Api-Sequence"] == "-1"
        assert request.headers["X-Api-Request-Id"]
        calls["query"] += 1
        if calls["query"] == 1:
            return httpx.Response(
                200,
                headers={"X-Api-Status-Code": "20000001"},
            )
        return httpx.Response(
            200,
            headers={"X-Api-Status-Code": "20000000"},
            json={
                "audio_info": {"duration": 12500},
                "result": {
                    "text": "你好，世界",
                    "utterances": [
                        {
                            "text": "你好，世界",
                            "start_time": 0,
                            "end_time": 12500,
                            "language": "zh-CN",
                        }
                    ],
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        provider = VolcengineASRProvider(
            api_key="api-key",
            poll_interval_seconds=0,
            http=client,
        )
        result = await provider.transcribe(
            AudioRef(key="sample.wav", local_path=audio)
        )

    assert calls["query"] == 2
    assert len(result.segments) == 1
    segment = result.segments[0]
    assert segment.text == "你好，世界"
    assert segment.language == "zh-CN"
    assert (segment.start_ms, segment.end_ms) == (0, 12500)
    assert result.usage is not None
    assert result.usage.provider == "volcengine-asr"
    assert result.usage.quantity == 12.5


async def test_volcengine_requires_credentials(tmp_path: Path) -> None:
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"fake")
    provider = VolcengineASRProvider(api_key="")
    with pytest.raises(RuntimeError, match="VOLCENGINE_ASR_API_KEY"):
        await provider.transcribe(AudioRef(key="sample.wav", local_path=audio))


async def test_volcengine_rejects_inline_oversize(tmp_path: Path) -> None:
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"too large")
    provider = VolcengineASRProvider(
        api_key="api-key",
        max_inline_bytes=1,
    )
    with pytest.raises(ValueError, match="too large"):
        await provider.transcribe(AudioRef(key="sample.wav", local_path=audio))
