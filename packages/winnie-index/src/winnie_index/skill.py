"""The Agent Skill that declares the index to coding agents.

The skill is a pointer, not a manual: it names the MCP tools and the HTTP
endpoint, states the citation rule, and stops. Teaching an agent which
directories exist or how to grep them would go stale the day a root changes.
"""

from __future__ import annotations

from pathlib import Path

from winnie_index.config import Config

SKILL_FILE = "SKILL.md"


def render(config: Config) -> str:
    base = f"http://{config.http_host}:{config.http_port}"
    return f"""\
---
name: local-knowledge
description: Search the owner's local notes and Markdown documents. Use when a
  question depends on what the owner wrote, decided or saved before ("我的笔记",
  "之前写的", "找一下那份文档", "where did I", "what did we decide about").
  Backed by an MCP server named local-knowledge and an HTTP API on
  {config.http_host}:{config.http_port}; never search the filesystem for these
  documents.
whenToUse: The user asks about something they wrote, noted, decided, or saved
  locally, or asks you to find a document without naming a path.
---

# Local knowledge

An index of the owner's own `.md` and `.txt` files. Reach it through tool calls,
not by walking directories.

## Tools

- `search_documents(query, roots?, since?, limit?)` — ranked hits with path,
  heading, line range, mtime and score.
- `get_document(path, from_line?, lines?)` — the cited text, revalidated against
  the file before it is returned.
- `index_status()` — coverage, last sweep, failures.

## HTTP

When MCP is unavailable: `POST {base}/search` with
`{{"query": "...", "limit": 8}}`.

## Rules

- Answer only from text returned by `get_document`; cite `path:line`.
- An empty result means the corpus has nothing. Say so instead of guessing.
- Results carry `mtime`; re-run the search after a file changes.
"""


def install(config: Config, target: Path | None = None) -> Path:
    destination = (target or config.skill_target).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / SKILL_FILE
    path.write_text(render(config), encoding="utf-8")
    path.chmod(0o644)
    return path


def check(config: Config, target: Path | None = None) -> tuple[bool, Path]:
    destination = (target or config.skill_target).expanduser() / SKILL_FILE
    if not destination.is_file():
        return False, destination
    return destination.read_text(encoding="utf-8") == render(config), destination
