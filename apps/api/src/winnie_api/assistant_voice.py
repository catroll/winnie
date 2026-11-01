"""Short-utterance speech to text for the voice chats.

Two clients use this: the phone, where the wake word is detected on-device, and
the admin console, where the owner starts a turn with the microphone button.
Neither streams: the endpoint receives one recorded utterance and returns its
text. It reuses the meeting-notes ASR binding (``meetings.transcribe``) so
switching the supplier in the console changes every voice chat at once, and the
call is visible in the LLM usage ledger through the same adapter.

The phone uploads 16 kHz mono WAV and the console encodes the same format from
its AudioWorklet capture, but the content type decides the suffix because a
vendor adapter may accept a compressed format and a caller may send one.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from winnie_core.auth.dependencies import get_user_id
from winnie_core.types import AudioRef
from winnie_llm import AIProviderError, ASRError, ASRNoSpeech

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])

# One spoken turn, not a meeting. The longest hands-free utterance this app
# records is 30 seconds (~1 MB at 16 kHz mono), so this is a backstop against a
# client bug, not a product limit.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Accepted upload types and the suffix the adapter sees. An unknown type is
# treated as WAV because that is what the client sends; a wrong suffix would
# only affect the adapter's container sniffing.
AUDIO_SUFFIXES = {
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
}


class UtteranceRead(BaseModel):
    """What the phone puts into the chat composer after a voice turn."""

    text: str


def _suffix(content_type: str | None) -> str:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    return AUDIO_SUFFIXES.get(mime, ".wav")


@router.post("/transcribe", response_model=UtteranceRead)
async def transcribe_utterance(
    request: Request,
    file: UploadFile = File(...),
    _: str = Depends(get_user_id),
) -> UtteranceRead:
    """Transcribe one recorded utterance and return plain text.

    Errors are deliberately distinct: 503 means the feature has no usable ASR
    binding, 502 means the vendor call failed, 422 means the vendor answered
    with no speech. The client shows the same sentence for all three, but the
    console and the log can tell them apart.

    "No speech" reached this endpoint as a 500 for a while, because the adapter
    signalled every vendor failure as a bare ``RuntimeError`` and this handler
    only knew ``AIProviderError``. The status code is the contract the client
    and the log rely on, so the adapter now raises :class:`ASRNoSpeech` and
    :class:`ASRError` and both are mapped here.
    """
    registry = getattr(request.app.state, "ai_settings", None)
    http = getattr(request.app.state, "http", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="the AI registry is disabled")

    audio = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(audio) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"upload exceeds the {MAX_UPLOAD_BYTES} byte limit",
        )
    if not audio:
        raise HTTPException(status_code=400, detail="the upload is empty")

    # The size is the only thing that tells a 1.5 s silent turn apart from a
    # real utterance after the fact; the audio itself is deleted below.
    turn = f"{len(audio)} bytes of {file.content_type or 'unknown type'}"

    path = _write_temp(audio, _suffix(file.content_type))
    try:
        try:
            provider = registry.asr_for("meetings.transcribe", http=http)
            result = await provider.transcribe(
                AudioRef(
                    key=path.name,
                    local_path=path,
                    content_type=file.content_type,
                ),
                language_hint=None,
            )
        except ASRNoSpeech as exc:
            logger.warning("voice turn held no speech (%s): %s", turn, exc)
            raise HTTPException(
                status_code=422, detail="no speech was recognized"
            ) from exc
        except ASRError as exc:
            logger.error("voice turn failed to transcribe (%s): %s", turn, exc)
            raise HTTPException(
                status_code=502, detail=f"the ASR provider failed: {exc}"
            ) from exc
        except AIProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "voice turn could not reach the ASR provider (%s): %s", turn, exc
            )
            raise HTTPException(
                status_code=502, detail=f"the ASR provider failed: {exc}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)

    text = " ".join(
        segment.text.strip() for segment in result.segments if segment.text.strip()
    )
    if not text:
        raise HTTPException(status_code=422, detail="no speech was recognized")
    return UtteranceRead(text=text)


def _write_temp(audio: bytes, suffix: str) -> Path:
    """Store the upload where a local adapter can read it, and nowhere else."""
    with tempfile.NamedTemporaryFile(
        prefix="winnie-voice-", suffix=suffix, delete=False
    ) as handle:
        handle.write(audio)
        return Path(handle.name)
