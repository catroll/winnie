"""Small file helpers shared by the conversation and memory stores.

Everything the assistant writes is a UTF-8 Markdown file with optional YAML
front matter. Writes go through a temporary file and ``os.replace`` so a crash
never leaves a half-written note behind.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

_FRONT_MATTER = re.compile(r"\A---\n(?P<header>.*?)\n---\n?", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->")


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Return ``(front_matter, body)``; a file without it yields ``({}, text)``."""
    match = _FRONT_MATTER.match(text)
    if match is None:
        return {}, text
    try:
        data = yaml.safe_load(match.group("header"))
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, text[match.end() :]


def render_front_matter(data: dict[str, Any]) -> str:
    """Render a flat mapping as YAML front matter with string values quoted.

    The values here are created by this package (ids, dates, categories,
    titles), and quoting every string keeps a title containing ``:`` or ``#``
    from changing the document's meaning.
    """
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        else:
            rendered = json.dumps(str(value), ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def strip_markup(text: str) -> str:
    """Remove inline HTML comments and tags, keeping the visible text."""
    return _TAG.sub("", _COMMENT.sub("", text))


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def format_time(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
