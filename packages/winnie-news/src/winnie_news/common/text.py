"""Text normalisation shared by every stage.

News material arrives as HTML fragments, escaped entities, zero-width
characters and inconsistent whitespace. Everything that writes text into a
schema goes through :func:`clean_text` so two stages cannot disagree about what
"the title" is.
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any

# Tags, plus the invisible characters that survive an HTML-to-text pass.
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
SCRIPT_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)


def strip_tags(fragment: str) -> str:
    """Remove HTML tags and unescape entities."""
    return unescape(TAG_RE.sub("", fragment)).strip()


def clean_text(fragment: str) -> str:
    """Remove scripts, tags and invisible characters; collapse whitespace."""
    text = SCRIPT_RE.sub(" ", fragment)
    text = strip_tags(text)
    text = ZERO_WIDTH_RE.sub("", text)
    text = CONTROL_RE.sub(" ", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def clean_body(fragment: str, *, limit: int = 0) -> str:
    """Clean a body while keeping paragraph breaks.

    A news body is more useful with its structure; collapsing every line break
    turns a multi-paragraph article into one unreadable line, so this keeps
    single newlines and trims runs of blank lines to one.
    """
    text = SCRIPT_RE.sub(" ", fragment)
    text = strip_tags_keeping_breaks(text)
    text = ZERO_WIDTH_RE.sub("", text)
    text = CONTROL_RE.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip()
    return text


def strip_tags_keeping_breaks(fragment: str) -> str:
    """Turn block-level tags into newlines before stripping the rest."""
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", fragment, flags=re.IGNORECASE)
    text = re.sub(
        r"</\s*(p|div|li|h[1-6]|section|article)\s*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    return unescape(TAG_RE.sub("", text)).strip()


def decode_text(raw: bytes, charset: str | None = None) -> str:
    """Decode a response body, replacing undecodable bytes instead of raising."""
    return raw.decode(charset or "utf-8", "replace")


def decode_json(raw: bytes, charset: str | None = None) -> Any:
    """Parse JSON, raising ``ValueError`` so the HTTP retry loop can decide."""
    try:
        return json.loads(decode_text(raw, charset))
    except json.JSONDecodeError as exc:
        raise ValueError(f"response body is not JSON: {exc}") from exc
