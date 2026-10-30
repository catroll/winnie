"""LLM-backed structured minutes over an OpenAI-compatible chat endpoint."""

from __future__ import annotations

import json
from typing import Any

from winnie_api.integrations.llm.json import parse_json_value
from winnie_api.integrations.llm.openai_compatible import OpenAICompatibleLLM
from winnie_api.integrations.summarize.base import (
    ActionItem,
    Decision,
    Highlight,
    MinutesDraft,
    Summarizer,
    Topic,
    TranscriptLine,
    TranscriptView,
)
from winnie_api.integrations.types import Usage


class LLMSummarizer(Summarizer):
    def __init__(self, llm: OpenAICompatibleLLM) -> None:
        self.llm = llm

    async def summarize(self, transcript: TranscriptView) -> MinutesDraft:
        if not transcript.lines:
            raise ValueError("cannot summarize an empty transcript")
        completion = await self.llm.complete(_messages(transcript))
        return _parse_draft(completion.text, transcript, usage=completion.usage)


def _messages(transcript: TranscriptView) -> list[dict[str, str]]:
    lines = "\n".join(_render_line(line) for line in transcript.lines)
    schema = {
        "summary": "string",
        "topics": [{"title": "string", "points": ["string"]}],
        "decisions": [{"text": "string", "context": "string"}],
        "action_items": [
            {
                "text": "string",
                "owner": "string or null",
                "due_date": "YYYY-MM-DD or null",
            }
        ],
        "highlights": [{"segment_seq": 1, "text": "string"}],
    }
    return [
        {
            "role": "system",
            "content": (
                "You turn meeting transcripts into faithful structured minutes. "
                f"Write the minutes in language code {transcript.local_language}. "
                "Do not invent owners, dates, decisions or facts that are not in "
                "the transcript. Return only one JSON object with the requested "
                "keys, no Markdown. Highlights must reference an existing "
                "segment_seq and quote or closely paraphrase that line."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Meeting title: {transcript.meeting_title}\n"
                f"Required JSON shape:\n{json.dumps(schema, ensure_ascii=False)}\n"
                f"Transcript:\n{lines}"
            ),
        },
    ]


def _render_line(line: TranscriptLine) -> str:
    parts = [f"[{line.seq}] language={line.detected_language or 'unknown'}"]
    parts.append(f"original: {line.text}")
    if line.translated_text:
        parts.append(f"translation: {line.translated_text}")
    return "\n".join(parts)


def _parse_draft(
    text: str,
    transcript: TranscriptView,
    *,
    usage: Usage,
) -> MinutesDraft:
    data = parse_json_value(text)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError("summarizer did not return a JSON object")

    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise RuntimeError("summarizer returned an empty summary")

    valid_lines = {line.seq: line for line in transcript.lines}
    topics: list[Topic] = []
    for item in _dicts(data.get("topics")):
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        topics.append(
            Topic(
                title=title,
                points=[
                    str(point).strip()
                    for point in _list(item.get("points"))
                    if str(point).strip()
                ],
            )
        )

    decisions: list[Decision] = []
    for item in _dicts(data.get("decisions")):
        value = str(item.get("text") or "").strip()
        if value:
            decisions.append(
                Decision(text=value, context=str(item.get("context") or "").strip())
            )

    action_items: list[ActionItem] = []
    for item in _dicts(data.get("action_items")):
        value = str(item.get("text") or "").strip()
        if not value:
            continue
        action_items.append(
            ActionItem(
                text=value,
                owner=_optional_string(item.get("owner")),
                due_date=_optional_string(item.get("due_date")),
            )
        )

    highlights: list[Highlight] = []
    for item in _dicts(data.get("highlights")):
        try:
            seq = int(item.get("segment_seq"))
        except (TypeError, ValueError):
            continue
        line = valid_lines.get(seq)
        if line is None:
            continue
        highlight_text = str(item.get("text") or "").strip() or line.text
        highlights.append(Highlight(segment_seq=seq, text=highlight_text))

    return MinutesDraft(
        summary=summary,
        topics=topics,
        decisions=decisions,
        action_items=action_items,
        highlights=highlights,
        usage=usage,
    )


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    return [item for item in _list(value) if isinstance(item, dict)]


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
