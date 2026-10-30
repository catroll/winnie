"""Deterministic summarizer for tests and local demos."""

from __future__ import annotations

from winnie_api.integrations.summarize.base import (
    ActionItem,
    Decision,
    Highlight,
    MinutesDraft,
    Summarizer,
    Topic,
    TranscriptView,
)
from winnie_api.integrations.types import Usage


class FakeSummarizer(Summarizer):
    async def summarize(self, transcript: TranscriptView) -> MinutesDraft:
        first_line = transcript.lines[0].text if transcript.lines else ""
        highlight_line = transcript.lines[1] if len(transcript.lines) > 1 else None
        return MinutesDraft(
            summary="会议确认先完成文件转写闭环，再投入实时流式能力。",
            topics=[
                Topic(
                    title="MVP 范围",
                    points=["文件转写优先", "实时转写延后到下一阶段"],
                )
            ],
            decisions=[
                Decision(
                    text="先以录音文件转写验证语种识别、翻译和纪要闭环。",
                    context="范围讨论",
                )
            ],
            action_items=[
                ActionItem(
                    text="完成上传、转写和纪要接口的联调。",
                    owner="catroll",
                    due_date="2026-10-31",
                )
            ],
            highlights=(
                [Highlight(segment_seq=highlight_line.seq, text=highlight_line.text)]
                if highlight_line
                else [Highlight(segment_seq=1, text=first_line)]
            ),
            usage=Usage(provider="fake-summarizer", unit="token", quantity=120.0),
        )
