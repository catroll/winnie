"""Long-term memory stored as editable Markdown, indexed in process.

The files are the truth; this module builds a lexical index from them at
startup and after every write. A deleted or edited entry is obeyed on the next
refresh, which is what makes manual correction meaningful.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from winnie_assistant.files import (
    atomic_write,
    collapse_whitespace,
    render_front_matter,
    split_front_matter,
    strip_markup,
)

CATEGORIES = (
    "identity",
    "preferences",
    "people",
    "projects",
    "domain",
    "procedures",
    "constraints",
    "resources",
    "episodes",
    "unsorted",
)

# Categories whose entries are always part of the system prompt. They are the
# stable facts that change how every answer is phrased or what it may not do.
PINNED_CATEGORIES = frozenset({"identity", "preferences", "constraints"})

_ASCII_WORD = re.compile(r"[a-z0-9_]+")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_COMMENT = re.compile(r"<!--\s*source:\s*(?P<source>.*?)\s*-->")
_DATE_PREFIX = re.compile(r"^\[(?P<date>\d{4}-\d{2}-\d{2})\]\s*")


@dataclass(frozen=True)
class MemoryEntry:
    relpath: str
    category: str
    title: str
    text: str
    line: int
    pinned: bool
    source: str = ""
    entry_date: str = ""


@dataclass(frozen=True)
class MemoryHit:
    entry: MemoryEntry
    score: float
    snippet: str


@dataclass(frozen=True)
class MemoryFile:
    relpath: str
    category: str
    title: str
    entries: int
    pinned: bool
    updated: str


def tokenize(text: str) -> list[str]:
    """Word tokens for Latin text, character bigrams for CJK.

    Chinese has no spaces, and a personal corpus does not justify a segmenter
    dependency; overlapping bigrams are a good enough unit for retrieval and
    they keep the implementation inspectable.
    """
    lowered = text.lower()
    tokens = [word for word in _ASCII_WORD.findall(lowered) if len(word) > 1]
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def normalize(text: str) -> str:
    return collapse_whitespace(text).lower()


class MemoryStore:
    def __init__(
        self,
        root: Path,
        *,
        extra_roots: tuple[Path, ...] = (),
        digest_chars: int = 2000,
    ) -> None:
        self.root = root
        self.extra_roots = tuple(path for path in extra_roots if path.is_dir())
        self.digest_chars = digest_chars
        self._entries: list[MemoryEntry] = []
        self._files: list[MemoryFile] = []
        self._idf: dict[str, float] = {}
        self._fingerprint: tuple | None = None

    # -- index -----------------------------------------------------------

    def refresh(self) -> None:
        fingerprint = self._scan_fingerprint()
        if fingerprint == self._fingerprint:
            return
        entries: list[MemoryEntry] = []
        files: list[MemoryFile] = []
        for root, writable in [(self.root, True), *[(p, False) for p in self.extra_roots]]:
            for path in sorted(root.rglob("*.md")):
                if any(part.startswith(".") for part in path.relative_to(root).parts):
                    continue
                relpath = self._relpath(path, root, writable)
                parsed, meta = _parse_file(path, relpath, writable)
                entries.extend(parsed)
                files.append(meta)
        self._entries = entries
        self._files = files
        document_frequency: Counter[str] = Counter()
        for entry in entries:
            document_frequency.update(set(tokenize(entry.text)) | set(tokenize(entry.title)))
        total = max(len(entries), 1)
        self._idf = {
            token: math.log((total + 1) / (count + 1)) + 1.0
            for token, count in document_frequency.items()
        }
        self._fingerprint = fingerprint

    def _scan_fingerprint(self) -> tuple:
        items: list[tuple[str, int, int]] = []
        for root in (self.root, *self.extra_roots):
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.md")):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                items.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(items)

    def _relpath(self, path: Path, root: Path, writable: bool) -> str:
        relative = path.relative_to(root).as_posix()
        return relative if writable else f"{root.name}/{relative}"

    @property
    def entries(self) -> tuple[MemoryEntry, ...]:
        self.refresh()
        return tuple(self._entries)

    def files(self) -> list[MemoryFile]:
        self.refresh()
        return list(self._files)

    # -- retrieval -------------------------------------------------------

    def search(self, query: str, limit: int = 5) -> list[MemoryHit]:
        self.refresh()
        tokens = tokenize(query)
        if not tokens:
            return []
        phrase = normalize(query)
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self._entries:
            haystack = normalize(entry.text)
            counts = {token: haystack.count(token) for token in tokens}
            hits = {token: count for token, count in counts.items() if count}
            if not hits:
                continue
            score = sum(
                (1.0 + math.log(count)) * self._idf.get(token, 1.0)
                for token, count in hits.items()
            )
            if phrase and phrase in haystack:
                score += 3.0
            if any(token in normalize(entry.title) for token in tokens):
                score += 1.5
            scored.append((score, entry))
        scored.sort(key=lambda item: -item[0])
        return [
            MemoryHit(entry=entry, score=round(score, 3), snippet=_snippet(entry.text, tokens))
            for score, entry in scored[:limit]
        ]

    def digest(self, limit: int | None = None) -> str:
        """The pinned memory that is always placed in the system prompt."""
        self.refresh()
        budget = self.digest_chars if limit is None else limit
        if budget <= 0:
            return ""
        lines: list[str] = []
        used = 0
        for category in CATEGORIES:
            pinned = [
                entry
                for entry in self._entries
                if entry.pinned and entry.category == category
            ]
            if not pinned:
                continue
            lines.append(f"### {category}")
            used += len(category) + 5
            for entry in pinned:
                line = f"- {entry.text}"
                if used + len(line) > budget:
                    lines.append("- … (more pinned memory omitted)")
                    return "\n".join(lines)
                lines.append(line)
                used += len(line) + 1
        return "\n".join(lines)

    # -- writes ----------------------------------------------------------

    def append(
        self,
        category: str,
        text: str,
        *,
        source: str,
        now: datetime | None = None,
    ) -> bool:
        """Append one fact. Returns False when the fact already exists."""
        if category not in CATEGORIES:
            raise ValueError(f"unknown memory category: {category}")
        fact = collapse_whitespace(strip_markup(text))
        fact = fact.replace("<!--", "").replace("-->", "").strip()
        if not fact:
            return False
        if len(fact) > 2000:
            fact = fact[:2000].rstrip()

        self.refresh()
        if any(normalize(entry.text) == normalize(fact) for entry in self._entries):
            return False

        stamp = (now or datetime.now()).date().isoformat()
        path = self.root / f"{category}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            header, body = split_front_matter(path.read_text(encoding="utf-8"))
        else:
            header, body = {}, f"# {category.title()}\n"

        header["category"] = category
        header.setdefault("pinned", category in PINNED_CATEGORIES)
        header["updated"] = stamp
        if not body.strip():
            body = f"# {category.title()}\n"
        body = body.rstrip() + f"\n- [{stamp}] {fact} <!-- source: {source} -->\n"
        atomic_write(path, render_front_matter(header) + body)
        self._fingerprint = None
        return True


def _parse_file(
    path: Path, relpath: str, writable: bool
) -> tuple[list[MemoryEntry], MemoryFile]:
    text = path.read_text(encoding="utf-8")
    header, body = split_front_matter(text)
    stem = path.stem
    category = str(header.get("category") or stem)
    # The front matter wins so a manual `pinned: true` or `false` is obeyed;
    # a file without it falls back to the category default.
    pinned = bool(header.get("pinned", category in PINNED_CATEGORIES))
    title = stem.title()
    entries: list[MemoryEntry] = []

    lines = body.splitlines()
    index = 0
    paragraph: list[str] = []
    paragraph_line = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            entries.append(
                _entry(relpath, category, title, " ".join(paragraph), paragraph_line, pinned)
            )
            paragraph = []

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("# "):
            flush_paragraph()
            title = stripped[2:].strip() or title
        elif stripped.startswith("- "):
            flush_paragraph()
            parts = [stripped[2:].strip()]
            start = index + 1
            index += 1
            while index < len(lines) and lines[index].startswith(("  ", "\t")):
                parts.append(lines[index].strip())
                index += 1
            entries.append(
                _entry(relpath, category, title, " ".join(parts), start, pinned)
            )
            continue
        elif not stripped:
            flush_paragraph()
        else:
            if not paragraph:
                paragraph_line = index + 1
            paragraph.append(stripped)
        index += 1
    flush_paragraph()

    updated = str(header.get("updated") or "")
    return entries, MemoryFile(
        relpath=relpath,
        category=category,
        title=title,
        entries=len(entries),
        pinned=pinned,
        updated=updated,
    )


def _entry(
    relpath: str,
    category: str,
    title: str,
    raw: str,
    line: int,
    pinned: bool,
) -> MemoryEntry:
    source_match = _COMMENT.search(raw)
    source = source_match.group("source") if source_match else ""
    cleaned = _COMMENT.sub("", raw)
    date_match = _DATE_PREFIX.match(cleaned.strip())
    entry_date = date_match.group("date") if date_match else ""
    text = cleaned.strip()
    if date_match:
        text = text[date_match.end() :].strip()
    return MemoryEntry(
        relpath=relpath,
        category=category,
        title=title,
        text=text,
        line=line,
        pinned=pinned,
        source=source,
        entry_date=entry_date,
    )


def _snippet(text: str, tokens: list[str], width: int = 120) -> str:
    lowered = text.lower()
    positions = [lowered.find(token) for token in tokens]
    found = [position for position in positions if position >= 0]
    start = max(0, (min(found) if found else 0) - width // 3)
    snippet = text[start : start + width].strip()
    if start > 0:
        snippet = "…" + snippet
    if start + width < len(text):
        snippet = snippet + "…"
    return snippet

