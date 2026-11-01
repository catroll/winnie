from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from winnie_api.main import create_app
from winnie_api.settings import AIConfig, DatabaseConfig, Settings
from winnie_core.config import JobsConfig
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
