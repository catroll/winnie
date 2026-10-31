"""Split text into chunks that can be cited.

Chunks break on structure — headings, paragraphs, fenced code — because a
citation is only useful if it points at a readable region. A chunk keeps the
heading path it sits under and its line range, and a code fence is never cut in
half unless it is larger than the whole budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from winnie_index.tokenize import count_tokens

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*\S)[ \t]*$")
_FENCE = re.compile(r"^[ \t]{0,3}(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    heading: str
    start_line: int
    end_line: int
    text: str
    tokens: int


@dataclass(frozen=True)
class _Block:
    start: int
    end: int
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip("\n")


@dataclass(frozen=True)
class _Section:
    heading: str
    blocks: list[_Block]


def _sections(lines: list[str]) -> list[_Section]:
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []
    blocks: list[_Block] = []
    block: list[str] = []
    block_start = 0
    in_fence = False

    def heading_path() -> str:
        return " > ".join(title for _, title in stack)

    def flush_block() -> None:
        nonlocal block, block_start
        if block and block[-1].strip():
            blocks.append(_Block(block_start, block_start + len(block) - 1, block))
        block = []

    def flush_section() -> None:
        nonlocal blocks
        flush_block()
        if blocks:
            sections.append(_Section(heading_path(), blocks))
        blocks = []

    for number, line in enumerate(lines, start=1):
        if _FENCE.match(line):
            if not block:
                block_start = number
            block.append(line)
            in_fence = not in_fence
            continue
        if in_fence:
            block.append(line)
            continue
        if not line.strip():
            flush_block()
            continue
        match = _HEADING.match(line)
        if match:
            flush_section()
            level = len(match.group(1))
            stack = [entry for entry in stack if entry[0] < level]
            stack.append((level, match.group(2).strip()))
            continue
        if not block:
            block_start = number
        block.append(line)

    flush_section()
    return sections


def _split_oversized(block: _Block, budget: int, overlap: int) -> list[_Block]:
    """Break one block that exceeds the budget, keeping line boundaries."""
    pieces: list[_Block] = []
    current: list[str] = []
    start = block.start
    tokens = 0
    for offset, line in enumerate(block.lines):
        line_tokens = count_tokens(line)
        if current and tokens + line_tokens > budget:
            pieces.append(_Block(start, start + len(current) - 1, current))
            tail: list[str] = []
            tail_tokens = 0
            for previous in reversed(current):
                previous_tokens = count_tokens(previous)
                if tail_tokens + previous_tokens > overlap:
                    break
                tail.insert(0, previous)
                tail_tokens += previous_tokens
            current = tail
            tokens = tail_tokens
            start = block.start + offset - len(tail)
        current.append(line)
        tokens += line_tokens
    if current:
        pieces.append(_Block(start, block.start + len(block.lines) - 1, current))
    return pieces


def chunk_markdown(
    text: str, *, max_tokens: int = 500, overlap: int = 60, max_chunks: int = 2000
) -> tuple[list[Chunk], bool]:
    """Return ``(chunks, truncated)`` for a Markdown or plain-text document."""
    lines = text.split("\n")
    chunks: list[Chunk] = []
    for section in _sections(lines):
        pending: list[_Block] = []
        tokens = 0

        def flush() -> None:
            nonlocal pending, tokens
            if not pending:
                return
            body = "\n\n".join(block.text for block in pending if block.text)
            if body.strip():
                chunks.append(
                    Chunk(
                        heading=section.heading,
                        start_line=pending[0].start,
                        end_line=pending[-1].end,
                        text=body,
                        tokens=count_tokens(body),
                    )
                )
            pending = []
            tokens = 0

        for block in section.blocks:
            block_tokens = count_tokens(block.text)
            if block_tokens > max_tokens:
                flush()
                for piece in _split_oversized(block, max_tokens, overlap):
                    body = piece.text
                    if body.strip():
                        chunks.append(
                            Chunk(
                                heading=section.heading,
                                start_line=piece.start,
                                end_line=piece.end,
                                text=body,
                                tokens=count_tokens(body),
                            )
                        )
                continue
            if pending and tokens + block_tokens > max_tokens:
                flush()
            pending.append(block)
            tokens += block_tokens
        flush()

    if len(chunks) > max_chunks:
        return chunks[:max_chunks], True
    return chunks, False
