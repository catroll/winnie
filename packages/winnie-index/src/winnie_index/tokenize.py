"""Tokens for a mixed Chinese/English corpus.

Chinese has no spaces, and a personal corpus does not justify a segmenter
dependency: overlapping bigrams are the unit. This matches
`winnie_assistant.memory.tokenize`, so the project keeps one tokenisation rule
instead of two.

The same function serves indexing and querying; a query segment with several
tokens becomes an FTS5 phrase, because the tokens are meant to be adjacent.
"""

from __future__ import annotations

import re

_ASCII_WORD = re.compile(r"[a-z0-9_]+")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
# Everything that is neither an ASCII word character nor a CJK ideograph
# separates query segments.
_SEGMENT_SEP = re.compile(r"[^a-z0-9_\u3400-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """Word tokens for Latin text, overlapping character bigrams for CJK."""
    lowered = text.lower()
    tokens = [word for word in _ASCII_WORD.findall(lowered) if len(word) > 1]
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def segments(query: str) -> list[str]:
    """Split a query on whitespace and punctuation, keeping CJK runs whole."""
    return [part for part in _SEGMENT_SEP.split(query.lower()) if part]


def _quote(token: str) -> str:
    """Quote one token for FTS5; the only special character is the quote."""
    return '"' + token.replace('"', '""') + '"'


def fts_query(query: str) -> str | None:
    """Build an FTS5 MATCH expression, or None when nothing is searchable."""
    parts: list[str] = []
    for segment in segments(query):
        tokens = tokenize(segment)
        if not tokens:
            continue
        if len(tokens) == 1:
            parts.append(_quote(tokens[0]))
        else:
            # One quoted phrase: inside the quotes FTS5 separates tokens by
            # spaces, so quoting each token again would break the expression.
            parts.append('"' + " ".join(tokens) + '"')
    return " AND ".join(parts) if parts else None


def raw_terms(query: str) -> list[str]:
    """Lowercased surface terms, used to keep hits that really contain them."""
    terms: list[str] = []
    for segment in segments(query):
        terms.extend(_ASCII_WORD.findall(segment))
        for run in _CJK_RUN.findall(segment):
            if len(run) <= 2:
                terms.append(run)
            else:
                terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def count_tokens(text: str) -> int:
    """Approximate token count: one per CJK character, one per four Latin chars."""
    cjk_chars = sum(len(run) for run in _CJK_RUN.findall(text))
    latin = len(text) - cjk_chars
    return cjk_chars + (latin + 3) // 4
