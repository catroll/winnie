"""Write meeting minutes into the personal knowledge base as Markdown."""

from __future__ import annotations

import asyncio
from pathlib import Path

from winnie_knowledge.models import MeetingNote, NoteContent


class MeetingNoteWriter:
    """Local Markdown sink. A Git-backed implementation can replace it later."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path_for(self, meeting: MeetingNote) -> Path:
        date = meeting.created_at.date().isoformat()
        return self.root / f"{date}-{meeting.id}.md"

    async def write(self, meeting: MeetingNote, content: NoteContent) -> Path:
        path = self.path_for(meeting)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            path.write_text, self.render(meeting, content), encoding="utf-8"
        )
        return path

    async def delete(self, meeting: MeetingNote) -> None:
        await asyncio.to_thread(self.path_for(meeting).unlink, missing_ok=True)

    def render(self, meeting: MeetingNote, content: NoteContent) -> str:
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
            content.summary or "_No summary generated._",
        ]

        if content.topics:
            lines.extend(["", "## Topics"])
            for topic in content.topics:
                lines.extend(["", f"### {topic.title}"])
                lines.extend(f"- {point}" for point in topic.points)

        if content.decisions:
            lines.extend(["", "## Decisions"])
            for decision in content.decisions:
                suffix = f" ({decision.context})" if decision.context else ""
                lines.append(f"- {decision.text}{suffix}")

        if content.action_items:
            lines.extend(["", "## Action items"])
            for item in content.action_items:
                owner = item.owner or "unassigned"
                due = item.due_date or "no due date"
                lines.append(f"- [ ] {item.text} (owner: {owner}, due: {due})")

        if content.highlights:
            lines.extend(["", "## Highlights"])
            for highlight in content.highlights:
                lines.append(f"- [{highlight.segment_seq}] {highlight.text}")

        return "\n".join(lines) + "\n"
