"""Long-term memory notes stored as editable Markdown.

The notes are the durable artefacts: one file per category, one bullet per
fact, with provenance in an HTML comment. Retrieval is **not** implemented
here; the local index service indexes these files and answers searches over
them, so the assistant keeps one retrieval implementation instead of two.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
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
#
# `procedures` belongs here for the same reason as `constraints`: a rule about
# *how* to answer ("no location given → use 武汉") is worthless if the only way
# to reach it is a search the model has to decide to run. On 2026-11-01 such a
# rule was written to procedures for exactly this request and then answered
# every weather question from the configured city instead.
PINNED_CATEGORIES = frozenset({"identity", "preferences", "procedures", "constraints"})

_COMMENT = re.compile(r"<!--\s*source:\s*(?P<source>.*?)\s*-->")
_DATE_PREFIX = re.compile(r"^\[(?P<date>\d{4}-\d{2}-\d{2})\]\s*")
_CJK = re.compile(r"[\u3400-\u9fff]+")
_WORD = re.compile(r"[a-z0-9_]+")
# Characters that carry grammar, not subject matter. Without this list a note is
# recalled by "的"/"我"/"了" and every turn gets the same unrelated notes.
_STOP_CHARS = frozenset(
    "的了是我你他她它在和就不吗呢吧啊么这那个们会能要把被给对去来为与及或也都还很太更最"
)


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
class MemoryFile:
    relpath: str
    category: str
    title: str
    entries: int
    pinned: bool
    updated: str


def normalize(text: str) -> str:
    return collapse_whitespace(text).lower()


# Punctuation and whitespace that do not change what a fact says. Two notes that
# differ only here are one fact: the distiller produced "若可获取经纬度，则…" and
# "若可获取经纬度则…" as separate bullets, and exact-string dedupe kept both.
_FACT_NOISE = re.compile(r"[\s，。；：、？！,.;:!?（）()【】\[\]“”\"'‘’《》<>·—…─-]+")


def _fact_key(text: str) -> str:
    return _FACT_NOISE.sub("", normalize(text))


def _recall_terms(text: str) -> tuple[set[str], set[str], set[str]]:
    """Overlapping bigrams, content characters and Latin words of a message."""
    lowered = text.lower()
    words = {word for word in _WORD.findall(lowered) if len(word) > 1}
    bigrams: set[str] = set()
    chars: set[str] = set()
    for run in _CJK.findall(lowered):
        if len(run) == 1:
            if run not in _STOP_CHARS:
                chars.add(run)
            continue
        bigrams.update(run[index : index + 2] for index in range(len(run) - 1))
        chars.update(char for char in run if char not in _STOP_CHARS)
    return bigrams, chars, words


def _match_counts(
    terms: tuple[set[str], set[str], set[str]], body: str
) -> tuple[int, int, int]:
    """How many bigrams, words and characters of ``terms`` occur in ``body``."""
    bigrams, chars, words = terms
    return (
        sum(1 for term in bigrams if term in body),
        sum(1 for word in words if word in body),
        sum(1 for char in chars if char in body),
    )


class MemoryStore:
    def __init__(
        self,
        root: Path,
        *,
        label: str = "profile",
        digest_chars: int = 2000,
    ) -> None:
        self.root = root
        self.label = label
        self.digest_chars = digest_chars
        self._entries: list[MemoryEntry] = []
        self._files: list[MemoryFile] = []
        self._fingerprint: tuple | None = None

    # -- index of the notes themselves ------------------------------------

    def refresh(self) -> None:
        fingerprint = self._scan_fingerprint()
        if fingerprint == self._fingerprint:
            return
        entries: list[MemoryEntry] = []
        files: list[MemoryFile] = []
        for path in sorted(self.root.glob("*.md")):
            relpath = f"{self.label}/{path.name}"
            parsed, meta = _parse_file(path, relpath)
            entries.extend(parsed)
            files.append(meta)
        self._entries = entries
        self._files = files
        self._fingerprint = fingerprint

    def _scan_fingerprint(self) -> tuple:
        items: list[tuple[str, int, int]] = []
        for path in sorted(self.root.glob("*.md")):
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(items)

    @property
    def entries(self) -> tuple[MemoryEntry, ...]:
        self.refresh()
        return tuple(self._entries)

    def files(self) -> list[MemoryFile]:
        self.refresh()
        return list(self._files)

    # -- prompt digest ----------------------------------------------------

    def everything(self, limit: int = 6000) -> str:
        """Every stored fact, for the distiller's "do not restate" context.

        ``digest`` is the per-turn view: pinned categories only, because that is
        what every request can afford. The distiller needs the opposite. A fact
        it cannot see is a fact it restates in different words, and the exact-key
        dedupe cannot catch a paraphrase — the owner's profile accumulated three
        phrasings of one weather rule and six of one piano lesson, and the
        categories that grew fastest (people, episodes) are not pinned at all,
        so they never appeared in the excerpt the extractor was given.
        """
        self.refresh()
        if limit <= 0:
            return ""
        lines: list[str] = []
        used = 0
        for category in CATEGORIES:
            entries = [entry for entry in self._entries if entry.category == category]
            if not entries:
                continue
            header = f"### {category}"
            if used + len(header) > limit:
                lines.append("… (more memory omitted)")
                break
            lines.append(header)
            used += len(header) + 1
            for entry in entries:
                line = f"- {entry.text}"
                if used + len(line) > limit:
                    lines.append("- … (more memory omitted)")
                    return "\n".join(lines)
                lines.append(line)
                used += len(line) + 1
        return "\n".join(lines)

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

    # -- per-turn recall --------------------------------------------------

    def recall(
        self,
        text: str,
        *,
        context: Sequence[str] = (),
        limit: int = 6,
        chars: int = 1200,
    ) -> str:
        """Unpinned notes relevant to this message and the conversation around it.

        This is the read path that does not go through the index: the notes are
        already parsed here, so a note written a second ago is visible, and a
        paraphrase is scored by overlap instead of by FTS phrase adjacency. It
        is a supplement, not a replacement — pinned memory is in the digest, and
        the index still answers "find the document that says X".

        ``context`` is the recent exchange (oldest first). A follow-up such as
        "那明天呢？" means nothing on its own, so the conversation carries a
        third of the weight: the message decides what is relevant now, the
        context keeps a subject alive across short turns.

        Matching is deliberately loose, because Chinese has no spaces and the
        caller's wording rarely repeats the note's. A shared bigram is worth more
        than a shared character, and a note must clear one of these bars: one
        bigram (or Latin word) shared with the message; one shared with the
        context when the message has no subject words of its own ("那几点？"); two
        shared with the context when the message does; or three shared characters
        with the message. Two shared common characters is what "帮我写个新闻稿"
        and "新写入内容" have in common, and that is noise.
        """
        self.refresh()
        if chars <= 0 or limit <= 0:
            return ""
        primary = _recall_terms(text)
        secondary = _recall_terms(" ".join(context)) if context else (set(), set(), set())
        if not any(primary) and not any(secondary):
            return ""
        scored: list[tuple[int, MemoryEntry]] = []
        for entry in self._entries:
            if entry.pinned:
                continue
            body = normalize(entry.text)
            primary_bigrams, primary_words, primary_chars = _match_counts(primary, body)
            context_bigrams, context_words, _ = _match_counts(secondary, body)
            from_message = primary_bigrams + primary_words
            from_context = context_bigrams + context_words
            if not (
                from_message
                or from_context >= (1 if not from_message else 2)
                or primary_chars >= 3
            ):
                continue
            score = (
                6 * primary_bigrams
                + 6 * primary_words
                + 2 * primary_chars
                + 2 * context_bigrams
                + 2 * context_words
            )
            scored.append((score, entry))
        if not scored:
            return ""
        scored.sort(key=lambda item: (-item[0], item[1].relpath, item[1].line))
        lines: list[str] = []
        used = 0
        for _, entry in scored[:limit]:
            line = f"- {entry.text}（{entry.relpath}:{entry.line}）"
            if used + len(line) > chars:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    # -- writes -----------------------------------------------------------

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
        key = _fact_key(fact)
        if any(_fact_key(entry.text) == key for entry in self._entries):
            return False

        stamp = (now or datetime.now()).date().isoformat()
        path = self.root / f"{category}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            header, body = split_front_matter(path.read_text(encoding="utf-8"))
        else:
            header, body = {}, f"# {category.title()}\n"

        header["category"] = category
        # Written, not defaulted: a file created before its category was pinned
        # still says `pinned: false`, and a human reading the note should see
        # the value the assistant actually uses.
        header["pinned"] = category in PINNED_CATEGORIES or bool(
            header.get("pinned", False)
        )
        header["updated"] = stamp
        if not body.strip():
            body = f"# {category.title()}\n"
        body = body.rstrip() + f"\n- [{stamp}] {fact} <!-- source: {source} -->\n"
        atomic_write(path, render_front_matter(header) + body)
        self._fingerprint = None
        return True


def _parse_file(path: Path, relpath: str) -> tuple[list[MemoryEntry], MemoryFile]:
    text = path.read_text(encoding="utf-8")
    header, body = split_front_matter(text)
    stem = path.stem
    category = str(header.get("category") or stem)
    # A pinned category is the contract: files written before a category was
    # pinned carry `pinned: false` in their front matter, and honouring that
    # would leave the new rule out of every prompt — exactly the failure this
    # set exists to prevent. Front matter can still *add* pinning to an
    # unpinned category, which is the manual override worth keeping.
    pinned = category in PINNED_CATEGORIES or bool(header.get("pinned", False))
    title = stem.title()
    entries: list[MemoryEntry] = []

    # Entries report their line in the *file*, not in the body: the front matter
    # and the heading are part of what a reader opens, and a citation that is
    # four lines off is worse than no citation.
    offset = len(text.split("\n")) - len(body.split("\n"))

    lines = body.splitlines()
    index = 0
    paragraph: list[str] = []
    paragraph_line = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            entries.append(
                _entry(
                    relpath,
                    category,
                    title,
                    " ".join(paragraph),
                    paragraph_line + offset,
                    pinned,
                )
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
                _entry(relpath, category, title, " ".join(parts), start + offset, pinned)
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
