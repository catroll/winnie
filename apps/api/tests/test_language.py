from __future__ import annotations

from winnie_llm import ASRResult, ASRSegment
from winnie_core.types import AudioRef, Usage
from winnie_meetings.language import detect_language
from winnie_api.runtime import build_runtime
from winnie_api.settings import Settings


class NoLanguageASR:
    async def transcribe(
        self,
        audio: AudioRef,
        *,
        language_hint: str | None = None,
    ) -> ASRResult:
        _ = audio, language_hint
        return ASRResult(
            segments=[
                ASRSegment(
                    text="大家好，今天我们讨论产品发布。",
                    start_ms=0,
                    end_ms=1000,
                ),
                ASRSegment(
                    text="Let's ship the API next week.",
                    start_ms=1000,
                    end_ms=2000,
                ),
                ASRSegment(text="12345", start_ms=2000, end_ms=3000),
            ],
            usage=Usage(provider="no-language-asr", unit="audio_second", quantity=3),
        )


def test_detect_language_by_script() -> None:
    chinese = detect_language("大家好，今天讨论发布计划")
    assert chinese is not None and chinese.code == "zh"

    chinese_with_acronyms = detect_language("我们使用 API 和 SDK 完成发布")
    assert chinese_with_acronyms is not None
    assert chinese_with_acronyms.code == "zh"

    english = detect_language("Let's ship the API next week")
    assert english is not None and english.code == "en"

    name_in_english = detect_language("I met 张伟 yesterday")
    assert name_in_english is not None and name_in_english.code == "en"

    japanese = detect_language("これはテストです")
    assert japanese is not None and japanese.code == "ja"

    korean = detect_language("안녕하세요 반갑습니다")
    assert korean is not None and korean.code == "ko"


def test_short_or_numeric_text_has_no_guess() -> None:
    assert detect_language("1234 !!") is None
    assert detect_language("a") is None
    assert detect_language("OK") is not None


async def test_pipeline_detects_language_when_asr_omits_it(
    meeting_settings: Settings,
) -> None:
    runtime = build_runtime(meeting_settings)
    assert runtime is not None and runtime.meeting_service is not None
    service = runtime.meeting_service
    service.asr = NoLanguageASR()

    await runtime.startup()
    try:
        meeting = await service.create_meeting(user_id="local", title="Bilingual")

        async def chunks():
            yield b"fake audio"

        await service.upload_audio(
            meeting.id,
            user_id="local",
            filename="sample.wav",
            chunks=chunks(),
        )
        await service.process(meeting.id)

        stored, segments = await service.get_transcript(
            meeting.id, user_id="local"
        )
        assert stored.status == "ready", stored.error
        assert [segment.detected_language for segment in segments] == [
            "zh",
            "en",
            "en",
        ]
        assert segments[0].translation_status == "skipped"
        assert segments[1].translation_status == "done"
        assert segments[1].translated_text is not None
        assert segments[1].translated_text.startswith("译文（zh）：")
        assert segments[2].translation_status == "done"
    finally:
        await runtime.shutdown()
