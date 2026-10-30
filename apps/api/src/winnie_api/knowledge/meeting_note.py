"""Write meeting minutes into the personal knowledge base as Markdown."""

from __future__ import annotations

import asyncio
from pathlib import Path

from winnie_api.integrations.summarize.base import MinutesDraft
from winnie_api.meetings.models import Meeting


class MeetingNoteWriter:
    """Local Markdown sink. A Git-backed implementation can replace it later."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path_for(self, meeting: Meeting) -> Path:
        date = meeting.created_at.date().isoformat()
        return self.root / f"{date}-{meeting.id}.md"

    async def write(self, meeting: Meeting, draft: MinutesDraft) -> Path:
        path = self.path_for(meeting)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, self.render(meeting, draft), encoding="utf-8")
        return path

    async def delete(self, meeting: Meeting) -> None:
        await asyncio.to_thread(self.path_for(meeting).unlink, missing_ok=True)

    def render(self, meeting: Meeting, draft: MinutesDraft) -> str:
        lines = [
            "---",
            "source: meeting",
            f"meeting_id: {meeting.id}",
            f"date: {meeting.created_at.date().isoformat()}",
            f"local_language: {meeting.local_language}",
            "tags: [from-meeting]",
            "---",
            "",
            f"# {meeting.title}",
            "",
            "## Summary",
            "",
            draft.summary or "_No summary generated._",
        ]

        if draft.topics:
            lines.extend(["", "## Topics"])
            for topic in draft.topics:
                lines.extend(["", f"### {topic.title}"])
                lines.extend(f"- {point}" for point in topic.points)

        if draft.decisions:
            lines.extend(["", "## Decisions"])
            for decision in draft.decisions:
                suffix = f" ({decision.context})" if decision.context else ""
                lines.append(f"- {decision.text}{suffix}")

        if draft.action_items:
            lines.extend(["", "## Action items"])
            for item in draft.action_items:
                owner = item.owner or "unassigned"
                due = item.due_date or "no due date"
                lines.append(f"- [ ] {item.text} (owner: {owner}, due: {due})")

        if draft.highlights:
            lines.extend(["", "## Highlights"])
            for highlight in draft.highlights:
                lines.append(f"- [{highlight.segment_seq}] {highlight.text}")

        return "\n".join(lines) + "\n"
