from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from winnie_api.main import create_app
from winnie_api.settings import AIConfig, DatabaseConfig, Settings
from winnie_core.config import JobsConfig
from winnie_llm import ASRError, ASRNoSpeech
from winnie_meetings.config import MeetingsConfig

# The smallest thing the endpoint accepts as audio. The fake ASR never opens it.
WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32


def _settings(tmp_path: Path, *, ai: bool) -> Settings:
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        ai=AIConfig(enabled=ai, path=str(tmp_path / "ai.yaml")),
        meetings=MeetingsConfig(
            enabled=True,
            local_root=str(tmp_path / "meetings"),
            notes_root=str(tmp_path / "notes"),
            max_upload_mb=1,
            asr_provider="fake",
        ),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )


async def _open(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


@pytest_asyncio.fixture
async def voice_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """A client whose registry binds a deterministic fake to meetings.transcribe."""
    async for client in _open(_settings(tmp_path, ai=True)):
        await client.post(
            "/api/v1/ai/vendors",
            json={"id": "test-tools", "label": "Test tools", "models": [{"id": "fake"}]},
        )
        await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "fake-asr",
                "vendor": "test-tools",
                "kind": "fake",
                "gateway": False,
            },
        )
        bound = await client.put(
            "/api/v1/ai/features/meetings.transcribe",
            json={"vendor": "test-tools", "model": "fake"},
        )
        assert bound.status_code == 200, bound.text
        yield client


@pytest.mark.asyncio
async def test_voice_turn_is_transcribed(voice_client: httpx.AsyncClient) -> None:
    response = await voice_client.post(
        "/api/v1/assistant/transcribe",
        files={"file": ("turn.wav", WAV_BYTES, "audio/wav")},
    )
    assert response.status_code == 200, response.text
    text = response.json()["text"]
    # The deterministic fake returns three segments; the endpoint joins them into
    # one line because the composer takes one message, not a transcript.
    assert "MVP" in text
    assert "realtime" in text
    assert " " in text


@pytest.mark.asyncio
async def test_an_empty_upload_is_rejected(voice_client: httpx.AsyncClient) -> None:
    response = await voice_client.post(
        "/api/v1/assistant/transcribe",
        files={"file": ("turn.wav", b"", "audio/wav")},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_a_missing_file_is_rejected(voice_client: httpx.AsyncClient) -> None:
    response = await voice_client.post("/api/v1/assistant/transcribe")
    assert response.status_code == 422


class _FailingASR:
    """A provider whose one job is to fail the way a vendor does."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def transcribe(self, audio, *, language_hint=None):  # noqa: ANN001
        _ = audio, language_hint
        raise self._error


class _FailingRegistry:
    """Stands in for the AI registry so the endpoint meets a typed ASR error."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def asr_for(self, feature: str, *, http=None):  # noqa: ANN001
        _ = feature, http
        return _FailingASR(self._error)


async def _open_with_provider(
    settings: Settings, error: Exception
) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        # The endpoint reads the registry per request, so replacing it here is
        # enough to drive one vendor answer without a network.
        app.state.ai_settings = _FailingRegistry(error)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_a_silent_turn_is_a_client_error(tmp_path: Path) -> None:
    """A vendor that decoded the audio and heard nothing is not a server fault.

    This used to answer 500 because the adapter signalled "no valid speech" as a
    bare RuntimeError the endpoint did not catch; the console showed a server
    error for what is really "you did not say anything".
    """
    error = ASRNoSpeech(
        "Volcengine ASR found no speech in the audio: "
        "code=20000003, message=[Normal silence audio]"
    )
    async for client in _open_with_provider(_settings(tmp_path, ai=True), error):
        response = await client.post(
            "/api/v1/assistant/transcribe",
            files={"file": ("turn.wav", WAV_BYTES, "audio/wav")},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == "no speech was recognized"


@pytest.mark.asyncio
async def test_a_vendor_failure_is_a_bad_gateway(tmp_path: Path) -> None:
    """Anything else the vendor rejects stays a 5xx, but a named one."""
    error = ASRError(
        "Volcengine ASR query failed: HTTP 403, code=45000030, "
        "message=requested resource not granted"
    )
    async for client in _open_with_provider(_settings(tmp_path, ai=True), error):
        response = await client.post(
            "/api/v1/assistant/transcribe",
            files={"file": ("turn.wav", WAV_BYTES, "audio/wav")},
        )
    assert response.status_code == 502
    assert "resource not granted" in response.json()["detail"]


@pytest.mark.asyncio
async def test_no_registry_does_not_mount_the_route(
    tmp_path: Path,
) -> None:
    """Without an AI registry there is no ASR binding, so the host omits the
    route instead of accepting an upload it could never transcribe."""
    async for client in _open(_settings(tmp_path, ai=False)):
        response = await client.post(
            "/api/v1/assistant/transcribe",
            files={"file": ("turn.wav", WAV_BYTES, "audio/wav")},
        )
    assert response.status_code == 404
