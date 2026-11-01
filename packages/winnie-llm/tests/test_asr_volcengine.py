"""The recording-file adapter has to keep two vendor answers apart.

"Normal silence audio" is a *successful* query whose body says the file holds no
speech; an entitlement or credential failure is a real error. Both used to leave
the adapter as a bare ``RuntimeError``, which the API host turned into HTTP 500
for what was only a quiet recording, so the exact code is asserted here.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from winnie_core.types import AudioRef
from winnie_llm.asr import ASRError, ASRNoSpeech, VolcengineASRProvider

SUCCESS = "20000000"
NO_SPEECH = "20000003"

# The smallest thing the adapter will accept as a file; it never decodes it.
WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32


def _transcriber(
    query_headers: dict[str, str], query_body: dict
) -> tuple[VolcengineASRProvider, httpx.AsyncClient]:
    """A provider whose submit always succeeds and whose query is scripted."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/submit"):
            return httpx.Response(200, headers={"X-Api-Status-Code": SUCCESS}, json={})
        return httpx.Response(200, headers=query_headers, json=query_body)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return VolcengineASRProvider(api_key="test-key", http=http), http


async def _transcribe(
    provider: VolcengineASRProvider, http: httpx.AsyncClient, tmp_path: Path
) -> None:
    audio = tmp_path / "turn.wav"
    audio.write_bytes(WAV_BYTES)
    async with http:
        await provider.transcribe(
            AudioRef(key=audio.name, local_path=audio, content_type="audio/wav")
        )


@pytest.mark.asyncio
async def test_a_silent_recording_is_not_a_provider_failure(tmp_path: Path) -> None:
    provider, http = _transcriber(
        {
            "X-Api-Status-Code": NO_SPEECH,
            "X-Api-Message": (
                "[Normal silence audio] Handle response: no valid speech in audio"
            ),
        },
        {},
    )
    with pytest.raises(ASRNoSpeech) as raised:
        await _transcribe(provider, http, tmp_path)
    assert "no valid speech" in str(raised.value)


@pytest.mark.asyncio
async def test_an_entitlement_failure_is_a_provider_error(tmp_path: Path) -> None:
    provider, http = _transcriber(
        {
            "X-Api-Status-Code": "45000030",
            "X-Api-Message": (
                "[resource_id=volc.seedasr.auc] requested resource not granted"
            ),
        },
        {},
    )
    with pytest.raises(ASRError) as raised:
        await _transcribe(provider, http, tmp_path)
    assert not isinstance(raised.value, ASRNoSpeech)
    assert "resource not granted" in str(raised.value)


@pytest.mark.asyncio
async def test_a_successful_query_with_no_text_is_no_speech(tmp_path: Path) -> None:
    provider, http = _transcriber(
        {"X-Api-Status-Code": SUCCESS}, {"result": {"utterances": []}}
    )
    with pytest.raises(ASRNoSpeech):
        await _transcribe(provider, http, tmp_path)


@pytest.mark.asyncio
async def test_transcribed_speech_returns_segments(tmp_path: Path) -> None:
    provider, http = _transcriber(
        {"X-Api-Status-Code": SUCCESS},
        {
            "result": {
                "utterances": [
                    {"text": "明天有雨么", "start_time": 0, "end_time": 1200}
                ]
            },
            "audio_info": {"duration": 1400},
        },
    )
    audio = tmp_path / "turn.wav"
    audio.write_bytes(WAV_BYTES)
    async with http:
        result = await provider.transcribe(
            AudioRef(key=audio.name, local_path=audio, content_type="audio/wav")
        )
    assert [segment.text for segment in result.segments] == ["明天有雨么"]
    assert result.usage is not None
