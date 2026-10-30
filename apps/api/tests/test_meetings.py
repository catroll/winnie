from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from winnie_api.main import create_app
from winnie_api.settings import Settings


@asynccontextmanager
async def _open_client(settings: Settings):
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
    await http.aclose()


async def _create_meeting(
    client: httpx.AsyncClient,
    title: str = "Weekly sync",
    **payload: object,
) -> dict:
    response = await client.post(
        "/api/v1/meetings", json={"title": title, **payload}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _upload_and_process(
    client: httpx.AsyncClient,
    meeting_id: str,
) -> dict:
    upload = await client.post(
        f"/api/v1/meetings/{meeting_id}/audio",
        files={"file": ("audio.wav", b"fake audio bytes", "audio/wav")},
    )
    assert upload.status_code == 200, upload.text
    response = await client.post(f"/api/v1/meetings/{meeting_id}/transcribe")
    assert response.status_code == 202, response.text
    return response.json()


async def test_meeting_lifecycle(
    meeting_client: httpx.AsyncClient,
    meeting_settings: Settings,
) -> None:
    meeting = await _create_meeting(meeting_client)
    meeting_id = meeting["id"]
    assert meeting["status"] == "created"

    upload = await meeting_client.post(
        f"/api/v1/meetings/{meeting_id}/audio",
        files={"file": ("audio.wav", b"fake audio bytes", "audio/wav")},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["status"] == "uploaded"

    transcribe = await meeting_client.post(
        f"/api/v1/meetings/{meeting_id}/transcribe"
    )
    assert transcribe.status_code == 202, transcribe.text
    assert transcribe.json()["status"] == "ready"
    assert transcribe.json()["stage"] == "done"
    assert transcribe.json()["audio_duration_ms"] == 10000

    transcript = await meeting_client.get(
        f"/api/v1/meetings/{meeting_id}/transcript"
    )
    assert transcript.status_code == 200
    segments = transcript.json()["segments"]
    assert len(segments) == 3
    assert segments[0]["detected_language"] == "zh"
    assert segments[0]["translated_text"] is None
    assert segments[0]["translation_status"] == "skipped"
    assert segments[1]["detected_language"] == "en"
    assert segments[1]["translated_text"].startswith("译文")
    assert segments[1]["translation_status"] == "done"

    minutes = await meeting_client.get(f"/api/v1/meetings/{meeting_id}/minutes")
    assert minutes.status_code == 200
    minutes_body = minutes.json()
    assert minutes_body["summary"]
    assert minutes_body["topics"][0]["title"] == "MVP 范围"
    assert minutes_body["action_items"][0]["owner"] == "catroll"
    assert minutes_body["model"] == "fake"

    regenerate = await meeting_client.post(
        f"/api/v1/meetings/{meeting_id}/minutes/regenerate"
    )
    assert regenerate.status_code == 200
    assert regenerate.json()["summary"] == minutes_body["summary"]

    export = await meeting_client.get(
        f"/api/v1/meetings/{meeting_id}/export",
        params={"format": "markdown"},
    )
    assert export.status_code == 200
    assert "# Weekly sync" in export.text
    assert "## Decisions" in export.text
    assert "attachment" in export.headers["content-disposition"]

    note_files = list(meeting_settings.resolve_notes_root().glob("*.md"))
    assert len(note_files) == 1
    note = note_files[0].read_text(encoding="utf-8")
    assert "source: meeting" in note
    assert "## Action items" in note

    listing = await meeting_client.get("/api/v1/meetings")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    deleted = await meeting_client.delete(f"/api/v1/meetings/{meeting_id}")
    assert deleted.status_code == 204
    missing = await meeting_client.get(f"/api/v1/meetings/{meeting_id}")
    assert missing.status_code == 404
    assert not list(meeting_settings.resolve_notes_root().glob("*.md"))


async def test_transcribe_requires_audio(meeting_client: httpx.AsyncClient) -> None:
    meeting = await _create_meeting(meeting_client)
    response = await meeting_client.post(
        f"/api/v1/meetings/{meeting['id']}/transcribe"
    )
    assert response.status_code == 409
    assert "upload audio" in response.json()["detail"]


async def test_translation_targets_only_non_local_segments(
    meeting_client: httpx.AsyncClient,
) -> None:
    meeting = await _create_meeting(meeting_client, local_language="en")
    processed = await _upload_and_process(meeting_client, meeting["id"])
    assert processed["status"] == "ready"

    segments = (
        await meeting_client.get(
            f"/api/v1/meetings/{meeting['id']}/transcript"
        )
    ).json()["segments"]
    assert segments[0]["detected_language"] == "zh"
    assert segments[0]["translated_text"].startswith("译文")
    assert segments[0]["translation_status"] == "done"
    assert segments[1]["detected_language"] == "en"
    assert segments[1]["translated_text"] is None
    assert segments[1]["translation_status"] == "skipped"


async def test_minutes_require_transcript(meeting_client: httpx.AsyncClient) -> None:
    meeting = await _create_meeting(meeting_client)
    minutes = await meeting_client.get(
        f"/api/v1/meetings/{meeting['id']}/minutes"
    )
    assert minutes.status_code == 409

    regenerate = await meeting_client.post(
        f"/api/v1/meetings/{meeting['id']}/minutes/regenerate"
    )
    assert regenerate.status_code == 409


async def test_upload_size_limit(meeting_client: httpx.AsyncClient) -> None:
    meeting = await _create_meeting(meeting_client)
    oversized = b"x" * (1024 * 1024 + 1)
    response = await meeting_client.post(
        f"/api/v1/meetings/{meeting['id']}/audio",
        files={"file": ("big.wav", oversized, "audio/wav")},
    )
    assert response.status_code == 413
    assert "byte limit" in response.json()["detail"]


async def test_unknown_meeting_returns_404(meeting_client: httpx.AsyncClient) -> None:
    response = await meeting_client.get(
        "/api/v1/meetings/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404


async def test_meetings_are_absent_when_disabled(settings: Settings) -> None:
    async with _open_client(settings) as client:
        response = await client.get("/api/v1/meetings")
        assert response.status_code == 404


def test_meeting_note_path_is_stable(tmp_path: Path) -> None:
    # Keeps the note writer's filename contract visible: date + meeting id.
    from winnie_api.knowledge.meeting_note import MeetingNoteWriter
    from winnie_api.meetings.models import Meeting, utcnow

    meeting = Meeting(
        title="x",
        user_id="local",
        local_language="zh",
        created_at=utcnow(),
    )
    writer = MeetingNoteWriter(tmp_path)
    path = writer.path_for(meeting)
    assert path.parent == tmp_path.resolve()
    assert path.name.endswith(f"{meeting.id}.md")
