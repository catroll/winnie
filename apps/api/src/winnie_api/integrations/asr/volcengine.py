"""Volcengine Doubao (big-model) recording-file ASR adapter.

The adapter submits a locally stored file as base64 to the big-model
recording-file API, then polls the query endpoint until the result is ready.
Credentials are read from the environment by the factory:

    VOLCENGINE_ASR_APP_ID
    VOLCENGINE_ASR_ACCESS_TOKEN

The endpoint and header names are vendor-specific. Keep the request/response
mapping in this module so a future vendor can be added behind the same port
without touching the meeting service.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from pathlib import Path

import httpx

from winnie_api.integrations.asr.base import ASRProvider, ASRResult, ASRSegment
from winnie_api.integrations.types import AudioRef, Usage

DEFAULT_BASE_URL = "https://openspeech.bytedance.com"
APP_ID_ENV = "VOLCENGINE_ASR_APP_ID"
ACCESS_TOKEN_ENV = "VOLCENGINE_ASR_ACCESS_TOKEN"
SUCCESS = "20000000"
PROCESSING = {"20000001", "20000002"}


class VolcengineASRProvider(ASRProvider):
    def __init__(
        self,
        *,
        app_id: str,
        access_token: str,
        base_url: str = DEFAULT_BASE_URL,
        resource_id: str = "volc.bigasr.auc",
        model_name: str = "bigmodel",
        max_inline_bytes: int = 100 * 1024 * 1024,
        poll_interval_seconds: float = 2.0,
        poll_timeout_seconds: float = 300.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self.resource_id = resource_id
        self.model_name = model_name
        self.max_inline_bytes = max_inline_bytes
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds
        self.http = http

    async def transcribe(
        self,
        audio: AudioRef,
        *,
        language_hint: str | None = None,
    ) -> ASRResult:
        self._require_credentials()
        _ = language_hint  # the big-model endpoint detects language per utterance
        path = audio.local_path
        if path is None:
            raise ValueError("Volcengine ASR needs a local audio file")

        content = await asyncio.to_thread(path.read_bytes)
        if len(content) > self.max_inline_bytes:
            raise ValueError(
                "audio is too large for inline Volcengine upload; "
                "use object storage or raise max_inline_mb"
            )

        request_id = str(uuid.uuid4())
        payload = {
            "user": {"uid": "winnie"},
            "audio": {
                "format": _audio_format(path),
                "data": base64.b64encode(content).decode("ascii"),
            },
            "request": {
                "model_name": self.model_name,
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
            },
        }

        client = self.http or httpx.AsyncClient(
            timeout=httpx.Timeout(self.poll_timeout_seconds + 30.0, connect=30.0)
        )
        try:
            submitted = await client.post(
                f"{self.base_url}/api/v3/auc/bigmodel/submit",
                headers=self._headers(request_id, sequence="-1"),
                json=payload,
            )
            self._raise_for_status(submitted, "submit")

            deadline = asyncio.get_running_loop().time() + self.poll_timeout_seconds
            while True:
                queried = await client.post(
                    f"{self.base_url}/api/v3/auc/bigmodel/query",
                    headers=self._headers(request_id, sequence="-1"),
                    json={},
                )
                code = queried.headers.get("X-Api-Status-Code", "")
                if code in ("", SUCCESS):
                    return self._parse(queried.json())
                if code in PROCESSING:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise RuntimeError(
                            f"Volcengine ASR timed out (request {request_id})"
                        )
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue
                self._raise_for_status(queried, "query")
                raise RuntimeError(
                    f"Volcengine ASR query failed with code {code!r}"
                )
        finally:
            if self.http is None:
                await client.aclose()

    def _require_credentials(self) -> None:
        if not self.app_id or not self.access_token:
            raise RuntimeError(
                "Volcengine ASR credentials are missing; set "
                f"{APP_ID_ENV} and {ACCESS_TOKEN_ENV}"
            )

    def _headers(
        self,
        request_id: str,
        *,
        sequence: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Api-App-Key": self.app_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": request_id,
        }
        if sequence is not None:
            headers["X-Api-Sequence"] = sequence
        return headers

    @staticmethod
    def _raise_for_status(response: httpx.Response, stage: str) -> None:
        code = response.headers.get("X-Api-Status-Code", "")
        message = (
            response.headers.get("X-Api-Message")
            or response.text[:300]
            or "no message"
        )
        if response.status_code >= 400 or (code and code != SUCCESS):
            raise RuntimeError(
                f"Volcengine ASR {stage} failed: "
                f"HTTP {response.status_code}, code={code or 'unknown'}, "
                f"message={message}"
            )

    def _parse(self, body: dict) -> ASRResult:
        result = body.get("result") or {}
        utterances = result.get("utterances") or []
        segments: list[ASRSegment] = []
        for utterance in utterances:
            text = str(utterance.get("text") or "").strip()
            if not text:
                continue
            segments.append(
                ASRSegment(
                    text=text,
                    language=utterance.get("language") or result.get("language"),
                    start_ms=int(utterance.get("start_time") or 0),
                    end_ms=int(utterance.get("end_time") or 0),
                )
            )

        if not segments:
            text = str(result.get("text") or "").strip()
            if text:
                segments.append(
                    ASRSegment(
                        text=text,
                        language=result.get("language"),
                        start_ms=0,
                        end_ms=int((body.get("audio_info") or {}).get("duration") or 0),
                    )
                )
        if not segments:
            raise RuntimeError("Volcengine ASR returned no text")

        duration_ms = int((body.get("audio_info") or {}).get("duration") or 0)
        if duration_ms <= 0:
            duration_ms = segments[-1].end_ms
        return ASRResult(
            segments=segments,
            usage=Usage(
                provider="volcengine-asr",
                unit="audio_second",
                quantity=duration_ms / 1000.0,
            ),
        )


def _audio_format(path: Path) -> str:
    suffix = path.suffix.lstrip(".").lower()
    return suffix or "mp3"
