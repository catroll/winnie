"""Helpers for extracting JSON from chat-model responses."""

from __future__ import annotations

import json
from typing import Any


def parse_json_value(text: str) -> Any:
    """Parse a JSON value, tolerating Markdown fences and short prose."""
    stripped = _strip_fence(text)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue
    preview = text.strip()[:200]
    raise ValueError(f"model response did not contain valid JSON: {preview!r}")


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
