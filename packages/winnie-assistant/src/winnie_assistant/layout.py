"""The assistant's directory layout on disk.

Everything the assistant knows lives here, next to the owner's other documents,
so the local index service can index it. The layout is created once and then
never restructured automatically; the README explains it to whoever opens the
directory.
"""

from __future__ import annotations

from pathlib import Path

from winnie_assistant.files import atomic_write

SUBDIRECTORIES = ("profile", "conversations", "tasks", ".state")

README = """\
# Winnie

This directory is the local memory of the Winnie assistant. Everything here is
plain Markdown or JSON that you can read, edit and back up; the assistant does
not hide state in a database.

| Path | What it is |
|---|---|
| `profile/` | Durable facts the assistant distilled about you: identity, preferences, people, projects, constraints. One bullet per fact, with the conversation it came from in a comment. Edit or delete lines freely. |
| `conversations/` | One Markdown file per conversation, grouped by date. User, assistant and tool messages are appended; earlier messages are not rewritten. |
| `tasks/tasks.json` | Scheduled tasks, written by the assistant when you ask for one in conversation. Ask the assistant to list or cancel tasks instead of editing this file. |
| `tasks/runs.jsonl` | One line per scheduled run: status, timing, conversation id and any error. |
| `.state/` | Derived bookkeeping: the memory watermark, the next-run state, the `exec.jsonl`/`files.jsonl` audit and `backups/` for overwritten files. The scheduler state is rebuilt; the audit and backups are records you may want to keep. |
| `README.md` | This file. |

The local index service (`winnie-index`) indexes the Markdown in this directory
together with your other documents, which is how the assistant recalls
something said weeks ago. New or changed files reach the index on its next
sweep, so a fact written seconds ago may not be searchable yet; the pinned
memory in `profile/` is read directly on every turn.

Scheduled tasks are managed by asking the assistant. It will confirm the
schedule and the content before creating one, and it can list or cancel tasks
on request.

The assistant can also list, read and write files in the configured workspace
and run local commands. Its own `profile/`, `conversations/`, `tasks/` and
`.state/` are refused by the file tools. Commands and overwrites return a
pending id and run only after you reply with an explicit confirmation in a
later turn; their audit is in `.state/`.
"""


def ensure_layout(root: Path) -> None:
    for name in SUBDIRECTORIES:
        (root / name).mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.is_file():
        atomic_write(readme, README)
