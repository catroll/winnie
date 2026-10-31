"""Extract a JSON object from a model reply.

Models wrap JSON in prose or in a fenced block even when told not to. A news
pipeline cannot afford a stage that dies on "Here is the JSON:" — the reply is
still usable — but it also must not accept a truncated object, so this scans for
the first balanced ``{...}`` that parses, and raises when nothing does.
"""

from __future__ import annotations

import json
from typing import Any


class JSONReplyError(ValueError):
    """The reply contained no parsable JSON object."""


def parse_json_object(text: str) -> dict[str, Any]:
    """Return the first parsable JSON object in ``text``; raise otherwise."""
    cleaned = _strip_fences(text).strip()
    direct = _try(cleaned)
    if direct is not None:
        return direct

    for start, char in enumerate(cleaned):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(cleaned)):
            current = cleaned[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    found = _try(cleaned[start : index + 1])
                    if found is not None:
                        return found
                    break
    raise JSONReplyError("model reply contained no JSON object")


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _try(candidate: str) -> dict[str, Any] | None:
    if not candidate.strip():
        return None
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None
