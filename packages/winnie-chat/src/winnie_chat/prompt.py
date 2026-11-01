"""System-prompt assembly for the chat engine."""

from __future__ import annotations

from winnie_chat.models import ChatTurn, Skill


def build_system_prompt(turn: ChatTurn, skills: list[Skill]) -> str:
    lines = [
        "You are Winnie, the personal assistant of the person who owns this",
        "self-hosted instance. Your files are under `Winnie/` in the owner's",
        "document root: `profile/` holds durable facts, `conversations/` holds",
        "every exchange, and `tasks/` holds scheduled tasks. A local index",
        "service indexes those files and the owner's other documents, so search",
        "is how you recall anything older than this conversation.",
        "",
        f"Current time: {turn.now.isoformat(timespec='minutes')} ({turn.timezone}).",
    ]
    if turn.scheduled:
        lines.extend(
            [
                "",
                "This turn was started by a scheduled task, not by a live message.",
                "Produce the brief the task asked for, ready to read on a phone.",
                "Do not create new scheduled tasks from inside a task run.",
                "Local file and command tools are not available in this turn.",
            ]
        )
    lines.extend(
        [
            "",
            "Long-term memory (Markdown files the user can edit; may be incomplete):",
            turn.memory_digest or "(empty)",
        ]
    )
    if turn.recalled_memory:
        lines.extend(
            [
                "",
                "Memory notes that may bear on this message (matched by wording, so",
                "some may be unrelated; cite path:line if you use one):",
                turn.recalled_memory,
            ]
        )
    if turn.phone_digest:
        lines.extend(
            [
                "",
                "Phone data synced from the owner's phone (a snapshot; the sync",
                "time is part of it):",
                turn.phone_digest,
            ]
        )
    if turn.pending:
        lines.extend(
            [
                "",
                "Pending action from an earlier turn (do not run it now unless the",
                "user's current message is an explicit confirmation):",
                turn.pending,
            ]
        )
    if skills:
        lines.extend(["", "Skills enabled for this turn:"])
        for skill in skills:
            instructions = skill.manifest.instructions.strip()
            if not instructions:
                continue
            lines.extend(
                [
                    "",
                    f"### {skill.manifest.title} ({skill.manifest.name})",
                    instructions,
                ]
            )
    return "\n".join(lines)
