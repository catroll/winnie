"""Nightly housekeeping of the memory notes.

The distiller appends facts one conversation at a time and never sees the whole
file, so the same rule arrives three times in slightly different words, a fake
model's placeholder text stays for ever, and a note that a later correction made
false keeps being recalled as true. The write path cannot fix that: ``append``
only refuses *exact* repeats, and asking the owner to edit the files by hand is
the thing this job exists to remove.

The pass is deterministic first and asks the model only about candidates:

1. Every pair of notes is compared by character bigrams (Chinese has no spaces)
   and by punctuation-insensitive identity. Near-duplicate clusters become merge
   candidates; nothing else is proposed, so a clean memory costs no model call.
2. Each candidate gets one call asking for a single merged sentence, or
   ``distinct``. Each file whose content changed gets one sweep asking which of
   its entries is dead — and *which words say so*.

An unattended job must not be able to forget a fact, so every applied change is
checked, not just logged:

- A merge must stay similar to **every** note it replaces, so it cannot quietly
  drop the phone number it was supposed to keep.
- A retirement must quote the entry verbatim as ``evidence``, must be at least
  ``retire_min_age_days`` old, and a ``duplicate``/``superseded`` retirement
  must name the surviving entry and be similar to it.
- The rewritten text is re-parsed before it is written; every note that was
  supposed to survive must be there, in order, word for word. Otherwise nothing
  is written at all.
- The pre-image of every touched file is copied to
  ``.state/memory-backups/<run>/`` together with the plan, and the run leaves a
  notice in the conversation store describing what changed.

Restoring a run is therefore a plain ``cp`` from the backup directory, and the
notice names it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from winnie_llm import parse_json_value

from winnie_assistant.agent import ChatModel
from winnie_assistant.config import MemoryTidyConfig
from winnie_assistant.conversations import ConversationStore
from winnie_assistant.files import (
    atomic_write,
    render_front_matter,
    split_front_matter,
    strip_markup,
)
from winnie_assistant.memory import (
    MemoryEntry,
    MemoryStore,
    fact_key,
    normalize,
    parse_memory,
)
from winnie_assistant.notify import Notifier
from winnie_assistant.scheduler import Schedule, parse_schedule

logger = logging.getLogger(__name__)

_CJK = re.compile(r"[\u3400-\u9fff]+")
_WORD = re.compile(r"[a-z0-9_]+")

# Removing a fact that nothing else states is how an owner loses a fact, so
# these two reasons must name the entry that still carries it.
SURVIVOR_REASONS = frozenset({"duplicate", "superseded"})
RETIRE_REASONS = frozenset({"duplicate", "superseded", "obsolete", "test_artifact"})

MERGE_PROMPT = """\
You clean up the long-term memory notes of a personal assistant.
The notes below were written at different times, and retrieval matched them as
saying the same thing.

Return JSON only, one of:
{{"merged": "<one note that states everything these notes state>"}}
{{"distinct": true}}

Rules:
- One self-contained sentence, under {max_chars} characters, in the language the
  notes are written in, with no leading bullet or date.
- Keep every durable detail any note states: names, numbers, places, dates and
  conditions. A merge that drops a detail is worse than no merge at all.
- The merged note is checked against the notes: every character that only one of
  them has (a name, a digit, a place) must appear in it. Merging three birthdays
  into one sentence that names all three people is fine; naming two of them is a
  deletion and is rejected. If one sentence cannot keep everything, answer
  {{"distinct": true}}.
- When the notes disagree about the same detail, keep the most recent wording:
  the owner corrected themselves later.
- Never invent a fact that is not in the notes, and never mention the notes
  themselves ("these notes say").
- They are genuinely different facts only when each carries information the
  other does not. Then answer {{"distinct": true}} and nothing else.
"""

SWEEP_PROMPT = """\
You clean up one memory file of a personal assistant.
Below are the file's notes, numbered. Most are still true and must stay.

Return JSON only:
{{"retire": [{{"index": 3, "reason": "obsolete", "evidence": "...",
             "survivor": null}}]}}

Allowed reasons:
- "test_artifact": placeholder or test text, not a fact about the owner.
- "obsolete": a one-off state that has passed, or a note a later note replaced.
- "duplicate": another note in this list states the same thing. Put that note's
  number in "survivor".
- "superseded": another note in this list corrects it. Put that note's number in
  "survivor".

Rules:
- "evidence" must be copied word for word from the entry being retired, at least
  3 characters. Do not paraphrase and do not summarise: it is checked against
  the entry text, and the retirement is dropped when it does not appear.
- "survivor" is required for duplicate and superseded, and must be one of the
  numbers below, not the retired one.
- Deleting something the owner stated is the worst outcome. When unsure, leave
  it out. An empty {{"retire": []}} is a good answer.
- At most {max_retire} entries.
"""


class TidyError(RuntimeError):
    """A change could not be made safely; the caller reports it."""


# -- similarity ----------------------------------------------------------


def _terms(text: str) -> set[str]:
    """Character bigrams and Latin words: what a note is *about*."""
    lowered = text.lower()
    terms = {word for word in _WORD.findall(lowered) if len(word) > 1}
    for run in _CJK.findall(lowered):
        if len(run) > 1:
            terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def dice(left: str, right: str) -> float:
    """Sørensen–Dice coefficient over term sets, in ``[0, 1]``."""
    first, second = _terms(left), _terms(right)
    if not first or not second:
        return 0.0
    return 2 * len(first & second) / (len(first) + len(second))


def _same_fact(left: str, right: str) -> bool:
    """Whether two notes are candidates for one fact."""
    first, second = fact_key(left), fact_key(right)
    if first == second:
        return True
    if len(first) >= 8 and len(second) >= 8:
        if first in second or second in first:
            return True
    return False


# Characters a rephrasing may drop or swap without changing what the note says.
# Everything else is content: a name, a place, a digit, a number.
_FUNCTION_CHARS = frozenset(
    "的了是在和与也都要会时若则就而并且或很太更最吗呢吧啊么这那个们我你他她它之其"
    "以为对从到把被"
)


def _content(text: str) -> tuple[set[str], set[str]]:
    """The content characters and Latin words of a note."""
    lowered = text.lower()
    chars = {
        char
        for run in _CJK.findall(lowered)
        for char in run
        if char not in _FUNCTION_CHARS
    }
    # A single digit is content: dropping it is how a merged note loses the
    # day of an appointment or the last digit of a phone number.
    words = {
        word
        for word in _WORD.findall(lowered)
        if len(word) > 1 or word.isdigit()
    }
    return chars, words


def _lost_content(cluster: tuple[Note, ...], merged: str) -> set[str]:
    """What only one source had and the merged note does not.

    This is the guard that stops a merge from choosing which fact to keep. Three
    notes whose wording is identical except for the name — three birthdays, say
    — overlap heavily, so a similarity threshold alone accepts a merged sentence
    that names one of the three and silently forgets the other two. Digits count
    as content, so a merged note cannot drop a date or a phone number either.
    """
    merged_chars, merged_words = _content(merged)
    lost: set[str] = set()
    for note in cluster:
        chars, words = _content(note.text)
        sibling_chars: set[str] = set()
        sibling_words: set[str] = set()
        for other in cluster:
            if other is note:
                continue
            other_chars, other_words = _content(other.text)
            sibling_chars |= other_chars
            sibling_words |= other_words
        lost |= (chars - sibling_chars) - merged_chars
        lost |= (words - sibling_words) - merged_words
    return lost


@dataclass(frozen=True)
class Note:
    """One entry, with the position it occupies in its file."""

    relpath: str
    category: str
    index: int
    text: str
    date: str
    source: str
    pinned: bool


@dataclass
class _FileState:
    relpath: str
    path: Path
    stem: str
    text: str
    header: dict[str, Any]
    lines: list[str]
    blocks: list[tuple[int, int]]
    notes: list[Note]


@dataclass(frozen=True)
class _Replacement:
    text: str
    date: str
    source: str


@dataclass(frozen=True)
class _Action:
    """One applied change, kept for the notice and the plan file."""

    kind: str
    relpath: str
    notes: tuple[Note, ...]
    text: str = ""
    reason: str = ""
    evidence: str = ""
    survivor: str = ""


@dataclass(frozen=True)
class TidyResult:
    status: str
    checked_at: datetime
    merges: int = 0
    retired: int = 0
    files: tuple[str, ...] = ()
    message: str = ""
    conversation_id: str = ""
    error: str = ""
    rejected: int = 0


class MemoryTidy:
    """Merge duplicates and retire dead notes on a schedule."""

    def __init__(
        self,
        *,
        config: MemoryTidyConfig,
        memory: MemoryStore,
        model: ChatModel,
        store: ConversationStore,
        notifier: Notifier | None,
        timezone: ZoneInfo,
        state_path: Path,
        backup_dir: Path,
        clock=None,
        tick_seconds: float | None = None,
    ) -> None:
        self.config = config
        self.memory = memory
        self.model = model
        self.store = store
        self.notifier = notifier
        self.timezone = timezone
        self.state_path = state_path
        self.backup_dir = backup_dir
        self.clock = clock or (lambda: datetime.now(timezone))
        self.tick_seconds = tick_seconds or config.tick_seconds
        self._schedule: Schedule = parse_schedule(config.schedule)
        self._state: dict[str, Any] = self._load()
        self._loop: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if not self._state.get("next_run"):
            self._state["next_run"] = _iso(self._schedule.next_after(self.clock()))
            self._save()
        self._loop = asyncio.create_task(self._run_loop(), name="memory-tidy")

    async def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()
            try:
                await self._loop
            except asyncio.CancelledError:
                pass
            self._loop = None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "schedule": self.config.schedule,
            "next_run": self._state.get("next_run"),
            "last_run": self._state.get("last_run"),
            "last_status": self._state.get("last_status"),
            "last_summary": self._state.get("last_summary") or "",
            "last_conversation_id": self._state.get("last_conversation_id"),
            "last_error": self._state.get("last_error") or "",
            "similarity": self.config.similarity,
            "retire_min_age_days": self.config.retire_min_age_days,
            "notify": self.config.notify,
        }

    async def tick(self, now: datetime | None = None) -> TidyResult | None:
        moment = now or self.clock()
        next_run = _parse_iso(self._state.get("next_run"))
        if next_run is None:
            self._state["next_run"] = _iso(self._schedule.next_after(moment))
            self._save()
            return None
        if next_run > moment:
            return None
        async with self._lock:
            return await self._run(moment, manual=False)

    async def run_now(self) -> TidyResult:
        """A manual run is an inspection: it does not move the schedule."""
        async with self._lock:
            return await self._run(self.clock(), manual=True)

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad night must not stop the loop
                logger.exception("memory tidy tick failed")
            await asyncio.sleep(self.tick_seconds)

    # -- the pass --------------------------------------------------------

    async def _run(self, moment: datetime, *, manual: bool) -> TidyResult:
        if not self.config.enabled:
            return self._finish(
                TidyResult(
                    status="disabled",
                    checked_at=moment,
                    error="memory tidy is disabled in the configuration",
                ),
                moment,
                advance=not manual,
            )
        states = self._load_files()
        if not states:
            return self._finish(
                TidyResult(status="no_change", checked_at=moment),
                moment,
                advance=not manual,
            )

        errors = 0
        rejected = 0
        sweeps: set[str] = set()
        actions: list[_Action] = []

        # Stage one: merges, and only from deterministic candidates.
        for cluster in self._candidates(states):
            try:
                merged = await self._merge(cluster)
            except Exception as exc:  # noqa: BLE001 - one bad answer, not a bad run
                logger.warning("memory tidy: merge call failed: %s", exc)
                errors += 1
                continue
            if merged is None:
                # "These are different facts" is an answer worth remembering:
                # the same notes would otherwise be offered again every night.
                self._decline(cluster, moment)
                continue
            problem = self._merge_problem(cluster, merged)
            if problem:
                logger.info("memory tidy: merge rejected (%s): %s", problem, merged)
                rejected += 1
                self._decline(cluster, moment)
                continue
            actions.append(
                _Action(
                    kind="merge",
                    relpath=cluster[0].relpath,
                    notes=tuple(cluster),
                    text=merged,
                )
            )

        # Stage two: retire, one sweep per new or stale file.
        for state, notes in self._sweep_targets(states, moment):
            try:
                retirements = await self._sweep(state, notes)
            except Exception as exc:  # noqa: BLE001 - a failed sweep keeps the file
                logger.warning(
                    "memory tidy: sweep of %s failed: %s", state.relpath, exc
                )
                errors += 1
                continue
            sweeps.add(state.relpath)
            for entry, reason, evidence, survivor in retirements:
                problem = self._retire_problem(
                    entry, reason, evidence, survivor, moment
                )
                if problem:
                    logger.info(
                        "memory tidy: retirement rejected (%s): %s",
                        problem,
                        entry.text[:60],
                    )
                    rejected += 1
                    continue
                actions.append(
                    _Action(
                        kind="retire",
                        relpath=entry.relpath,
                        notes=(entry,),
                        reason=reason,
                        evidence=evidence,
                        survivor=survivor.text if survivor is not None else "",
                    )
                )

        if not actions:
            return self._finish(
                TidyResult(
                    status="partial" if errors else "no_change",
                    checked_at=moment,
                    error=f"{errors} model call(s) failed" if errors else "",
                    rejected=rejected,
                ),
                moment,
                advance=not manual,
                stamps=sorted(sweeps),
            )

        try:
            changed = self._apply(states, actions, moment)
            run_id = moment.strftime("%Y%m%dT%H%M%S")
            self._backup(run_id, changed, actions, moment)
            self._write(changed)
        except TidyError as exc:
            logger.error("memory tidy refused to write: %s", exc)
            return self._finish(
                TidyResult(
                    status="error",
                    checked_at=moment,
                    error=str(exc)[:500],
                ),
                moment,
                advance=not manual,
            )
        self.memory.refresh()

        message = _summary(actions, run_id, changed, rejected)
        conversation_id = ""
        if not manual:
            conversation = await self.store.create_notice(
                title=f"记忆整理 · {moment.strftime('%m-%d')}",
                text=message,
                source="memory",
                channel="notification",
                now=moment,
            )
            conversation_id = conversation.id
            if self.config.notify and self.notifier is not None:
                try:
                    await self.notifier.notify(
                        title="记忆整理",
                        body=message[:1000],
                        data={
                            "kind": "memory_tidy",
                            "merges": str(
                                sum(1 for a in actions if a.kind == "merge")
                            ),
                            "retired": str(
                                sum(1 for a in actions if a.kind == "retire")
                            ),
                            "conversation_id": conversation.id,
                        },
                    )
                except Exception:  # noqa: BLE001 - a push failure is not a tidy failure
                    logger.warning("memory tidy push failed", exc_info=True)

        result = TidyResult(
            status="ok" if not errors else "partial",
            checked_at=moment,
            merges=sum(1 for action in actions if action.kind == "merge"),
            retired=sum(1 for action in actions if action.kind == "retire"),
            files=tuple(sorted(changed)),
            message=message,
            conversation_id=conversation_id,
            rejected=rejected,
            error=f"{errors} model call(s) failed" if errors else "",
        )
        return self._finish(
            result,
            moment,
            advance=not manual,
            stamps=sorted(sweeps | set(changed)),
        )

    # -- reading the notes -----------------------------------------------

    def _load_files(self) -> list[_FileState]:
        """Parse every memory file, and every entry's position in it.

        A file this pass cannot reproduce byte-for-byte apart from the lines it
        edits is skipped, not repaired: the job is a tidy, not a reformatter,
        and a file it does not understand is exactly the one it must not rewrite.
        """
        states: list[_FileState] = []
        for path in sorted(self.memory.root.glob("*.md")):
            relpath = f"{self.memory.label}/{path.name}"
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("memory tidy: %s is unreadable: %s", path, exc)
                continue
            header, body = split_front_matter(text)
            lines = body.split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            if body.strip() and body.rstrip("\n") != "\n".join(lines):
                logger.warning(
                    "memory tidy: %s has line breaks this pass cannot preserve; "
                    "leaving it alone",
                    path,
                )
                continue
            entries, _ = parse_memory(text, relpath=relpath, stem=path.stem)
            blocks = _blocks(lines)
            if len(blocks) != len(entries):
                logger.warning(
                    "memory tidy: %s parsed %d entries but found %d blocks; "
                    "leaving it alone",
                    path,
                    len(entries),
                    len(blocks),
                )
                continue
            notes = [_note(entry, index) for index, entry in enumerate(entries)]
            # The block scanner and the parser must agree about what an entry
            # is; otherwise the line edits are aimed at the wrong text and the
            # rewrite cannot be trusted.
            if any(
                fact_key(note.text) not in _block_text(lines, block)
                for note, block in zip(notes, blocks)
            ):
                logger.warning(
                    "memory tidy: %s has entries this pass cannot locate; "
                    "leaving it alone",
                    path,
                )
                continue
            states.append(
                _FileState(
                    relpath=relpath,
                    path=path,
                    stem=path.stem,
                    text=text,
                    header=dict(header),
                    lines=lines,
                    blocks=blocks,
                    notes=notes,
                )
            )
        return states

    def _candidates(self, states: list[_FileState]) -> list[tuple[Note, ...]]:
        """Near-duplicate clusters, biggest first, capped for one run."""
        notes = [note for state in states for note in state.notes]
        parent = list(range(len(notes)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            first, second = find(left), find(right)
            if first != second:
                parent[second] = first

        for left in range(len(notes)):
            for right in range(left + 1, len(notes)):
                if _same_fact(notes[left].text, notes[right].text) or (
                    dice(notes[left].text, notes[right].text) >= self.config.similarity
                ):
                    union(left, right)

        groups: dict[int, list[Note]] = {}
        for index, note in enumerate(notes):
            groups.setdefault(find(index), []).append(note)

        declined = self._state.get("declined") or {}
        clusters = [
            tuple(group)
            for group in groups.values()
            if 2 <= len(group) <= self.config.merge_batch
            and _fingerprint(group) not in declined
        ]
        clusters.sort(key=lambda group: (-len(group), group[0].relpath, group[0].index))
        return clusters[: self.config.max_merges_per_run]

    def _sweep_targets(
        self, states: list[_FileState], moment: datetime
    ) -> list[tuple[_FileState, list[Note]]]:
        """Files whose content changed since the last sweep, stalest first."""
        known = self._state.get("files") or {}
        resweep = timedelta(days=self.config.resweep_days)
        pending: list[tuple[datetime, _FileState]] = []
        for state in states:
            record = known.get(state.relpath) or {}
            swept_at = _parse_iso(record.get("swept_at"))
            unchanged = record.get("hash") == _hash_text(state.text)
            if unchanged and swept_at is not None and moment - swept_at < resweep:
                continue
            pending.append((swept_at or _EPOCH, state))
        # Oldest first, so the per-run cap rotates through every file instead
        # of sweeping the same prefix every night.
        pending.sort(key=lambda item: (item[0], item[1].relpath))
        return [
            (state, state.notes)
            for _, state in pending[: self.config.max_sweeps_per_run]
        ]

    # -- asking the model ------------------------------------------------

    async def _merge(self, cluster: tuple[Note, ...]) -> str | None:
        notes = "\n".join(
            f"{index}. [{note.date or 'undated'}] {note.text}"
            for index, note in enumerate(cluster, start=1)
        )
        value = await self._ask(
            MERGE_PROMPT.format(max_chars=self.config.max_fact_chars),
            f"Category: {cluster[0].category}\nNotes:\n{notes}",
        )
        if _true(value.get("distinct")):
            return None
        merged = " ".join(str(value.get("merged") or "").split()).strip()
        return merged or None

    async def _sweep(
        self, state: _FileState, notes: list[Note]
    ) -> list[tuple[Note, str, str, Note | None]]:
        shown = _fit(notes, self.config.max_file_chars)
        if not shown:
            return []
        listing = "\n".join(
            f"{index}. [{note.date or 'undated'}] {note.text}"
            for index, note in enumerate(shown, start=1)
        )
        value = await self._ask(
            SWEEP_PROMPT.format(max_retire=self.config.retire_max_per_run),
            f"File: {state.relpath} ({len(shown)} of {len(notes)} notes shown)\n"
            f"Notes:\n{listing}",
        )
        raw = value.get("retire")
        if not isinstance(raw, list):
            return []
        out: list[tuple[Note, str, str, Note | None]] = []
        for item in raw[: self.config.retire_max_per_run]:
            if not isinstance(item, dict):
                continue
            index = _as_index(item.get("index"))
            if index is None or not 1 <= index <= len(shown):
                continue
            entry = shown[index - 1]
            reason = str(item.get("reason") or "").strip().lower()
            evidence = " ".join(str(item.get("evidence") or "").split())
            survivor_index = _as_index(item.get("survivor"))
            survivor: Note | None = None
            if (
                survivor_index is not None
                and survivor_index != index
                and 1 <= survivor_index <= len(shown)
            ):
                survivor = shown[survivor_index - 1]
            out.append((entry, reason, evidence, survivor))
        return out

    async def _ask(self, system: str, user: str) -> dict[str, Any]:
        result = await self.model.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
        value = parse_json_value(result.text)
        if not isinstance(value, dict):
            raise TidyError("the model did not return a JSON object")
        return value

    # -- guardrails ------------------------------------------------------

    def _merge_problem(self, cluster: tuple[Note, ...], merged: str) -> str:
        if "<!--" in merged or "-->" in merged:
            return "contains a comment"
        if len(merged) > self.config.max_fact_chars:
            return "longer than max_fact_chars"
        lost = _lost_content(cluster, merged)
        if lost:
            return "would drop " + "".join(sorted(lost)[:8])
        for note in cluster:
            if dice(note.text, merged) < self.config.min_member_similarity:
                # A merge that does not resemble one of its sources has
                # replaced that fact rather than restating it.
                return f"too far from an original: {note.text[:40]!r}"
        return ""

    def _retire_problem(
        self,
        entry: Note,
        reason: str,
        evidence: str,
        survivor: Note | None,
        moment: datetime,
    ) -> str:
        if reason not in RETIRE_REASONS:
            return f"unknown reason {reason!r}"
        quoted = normalize(strip_markup(evidence))
        if len(quoted) < 3 or quoted not in normalize(entry.text):
            # The model must point at the words that justify the deletion. A
            # paraphrase means it is inventing the justification.
            return "evidence is not a quote from the entry"
        if self._too_recent(entry, moment):
            return "too recent to retire unattended"
        if reason in SURVIVOR_REASONS:
            if survivor is None:
                return "no surviving entry named"
            if dice(entry.text, survivor.text) < self.config.retire_similarity:
                return "the named survivor says something else"
        return ""

    def _too_recent(self, entry: Note, moment: datetime) -> bool:
        if not entry.date:
            return False
        try:
            recorded = date.fromisoformat(entry.date)
        except ValueError:
            return False
        return (moment.date() - recorded).days < self.config.retire_min_age_days

    # -- applying --------------------------------------------------------

    def _apply(
        self,
        states: list[_FileState],
        actions: list[_Action],
        moment: datetime,
    ) -> dict[str, str]:
        """Render, verify and return the new text of every changed file."""
        replacements: dict[str, dict[int, _Replacement | None]] = {}

        def place(note: Note, value: _Replacement | None) -> None:
            edits = replacements.setdefault(note.relpath, {})
            if note.index in edits:
                # A merge and a retirement disagreed about the same line. The
                # merge ran first and is the verified one, so keep it.
                logger.warning(
                    "memory tidy: two edits for %s entry %s; keeping the first",
                    note.relpath,
                    note.index,
                )
                return
            edits[note.index] = value

        for action in actions:
            if action.kind == "merge":
                host = _host(list(action.notes))
                place(
                    host,
                    _Replacement(
                        text=action.text,
                        date=max(
                            (note.date for note in action.notes), default=host.date
                        ),
                        source=f"tidy:merge:{moment.strftime('%Y-%m-%d')}",
                    ),
                )
                for note in action.notes:
                    if note is not host:
                        place(note, None)
            else:
                for note in action.notes:
                    place(note, None)

        changed: dict[str, str] = {}
        for state in states:
            edits = replacements.get(state.relpath)
            if not edits:
                continue
            text = _render(state, edits, moment.date().isoformat())
            expected = [
                edits[index].text
                if index in edits and edits[index] is not None
                else state.notes[index].text
                for index in range(len(state.notes))
                if not (index in edits and edits[index] is None)
            ]
            entries, _ = parse_memory(text, relpath=state.relpath, stem=state.stem)
            actual = [entry.text for entry in entries]
            if actual != expected:
                raise TidyError(
                    f"{state.relpath} would not survive its own rewrite: "
                    f"expected {len(expected)} notes, parsed {len(actual)}"
                )
            changed[state.relpath] = text
        return changed

    def _backup(
        self,
        run_id: str,
        changed: dict[str, str],
        actions: list[_Action],
        moment: datetime,
    ) -> None:
        directory = self.backup_dir / run_id
        for relpath in changed:
            atomic_write(directory / Path(relpath).name, self._before(relpath))
        plan = {
            "run_id": run_id,
            "at": _iso(moment),
            "files": sorted(changed),
            "actions": [
                {
                    "kind": action.kind,
                    "relpath": action.relpath,
                    "reason": action.reason,
                    "evidence": action.evidence,
                    "survivor": action.survivor,
                    "text": action.text,
                    "sources": [note.text for note in action.notes],
                }
                for action in actions
            ],
        }
        atomic_write(
            directory / "plan.json",
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        )
        self._prune_backups()

    def _before(self, relpath: str) -> str:
        return (self.memory.root / Path(relpath).name).read_text(encoding="utf-8")

    def _write(self, changed: dict[str, str]) -> None:
        written: list[tuple[Path, str]] = []
        try:
            for relpath, text in changed.items():
                path = self.memory.root / Path(relpath).name
                written.append((path, path.read_text(encoding="utf-8")))
                atomic_write(path, text)
        except OSError as exc:
            logger.error("memory tidy: write failed, restoring: %s", exc)
            for path, previous in written:
                try:
                    atomic_write(path, previous)
                except OSError:  # noqa: BLE001 - nothing else can be done here
                    logger.exception("memory tidy: could not restore %s", path)
            raise TidyError(f"writing the tidied files failed: {exc}") from exc

    def _prune_backups(self) -> None:
        try:
            directories = sorted(
                (path for path in self.backup_dir.iterdir() if path.is_dir()),
                key=lambda path: path.name,
            )
        except OSError:
            return
        for path in directories[: max(0, len(directories) - self.config.keep_backups)]:
            shutil.rmtree(path, ignore_errors=True)

    # -- state -----------------------------------------------------------

    def _finish(
        self,
        result: TidyResult,
        moment: datetime,
        *,
        advance: bool,
        stamps: list[str] | None = None,
    ) -> TidyResult:
        self._state["last_run"] = _iso(moment)
        self._state["last_status"] = result.status
        if result.message:
            self._state["last_summary"] = result.message
        if result.conversation_id:
            self._state["last_conversation_id"] = result.conversation_id
        if result.error:
            self._state["last_error"] = result.error
        else:
            self._state.pop("last_error", None)
        if stamps:
            self._stamp(stamps, moment)
        if advance:
            self._state["next_run"] = _iso(self._schedule.next_after(moment))
        self._save()
        return result

    def _decline(self, cluster: tuple[Note, ...], moment: datetime) -> None:
        """Remember a cluster the model said was distinct, or that was refused.

        The fingerprint covers the exact texts, so editing any member (or
        retiring one) makes the cluster a candidate again.
        """
        declined = dict(self._state.get("declined") or {})
        declined[_fingerprint(cluster)] = _iso(moment)
        if len(declined) > 200:
            keep = sorted(declined.items(), key=lambda item: item[1])[-200:]
            declined = dict(keep)
        self._state["declined"] = declined

    def _stamp(self, stamps: list[str], moment: datetime) -> None:
        """Record the files this run actually looked at.

        A file that was skipped because it could not be parsed, or never reached
        because of the per-run cap, must stay pending: stamping it would
        postpone its first real sweep for a month.
        """
        known = dict(self._state.get("files") or {})
        for relpath in stamps:
            path = self.memory.root / Path(relpath).name
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            known[relpath] = {"hash": _hash_text(text), "swept_at": _iso(moment)}
        self._state["files"] = known

    def _load(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("memory tidy state is unreadable; starting fresh")
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self) -> None:
        payload = {**self._state, "updated_at": _iso(self.clock())}
        atomic_write(
            self.state_path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


# -- text handling -------------------------------------------------------


def _blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Line spans of the entries, using the parser's own grouping rules.

    A bullet is ``- `` plus every following indented line; a paragraph is a run
    of non-blank, non-bullet lines. A heading or a blank line ends either.
    """
    blocks: list[tuple[int, int]] = []
    paragraph: int | None = None
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("- "):
            if paragraph is not None:
                blocks.append((paragraph, index))
                paragraph = None
            start = index
            index += 1
            while index < len(lines) and lines[index].startswith(("  ", "\t")):
                index += 1
            blocks.append((start, index))
            continue
        if stripped.startswith("# ") or not stripped:
            if paragraph is not None:
                blocks.append((paragraph, index))
                paragraph = None
        elif paragraph is None:
            paragraph = index
        index += 1
    if paragraph is not None:
        blocks.append((paragraph, len(lines)))
    return blocks


def _block_text(lines: list[str], block: tuple[int, int]) -> str:
    start, end = block
    first = lines[start].strip()
    parts = [first[2:].strip() if first.startswith("- ") else first]
    parts.extend(line.strip() for line in lines[start + 1 : end])
    return fact_key(" ".join(parts))


def _note(entry: MemoryEntry, index: int) -> Note:
    return Note(
        relpath=entry.relpath,
        category=entry.category,
        index=index,
        text=entry.text,
        date=entry.entry_date,
        source=entry.source,
        pinned=entry.pinned,
    )


def _host(members: list[Note]) -> Note:
    """Where a merged note lives: a pinned file first, then the newest note.

    Moving a rule out of a pinned category would quietly take it out of every
    prompt, which is the failure the pinning exists to prevent.
    """
    return max(members, key=lambda note: (note.pinned, note.date))


def _fit(notes: list[Note], budget: int) -> list[Note]:
    """The prefix of ``notes`` that fits one prompt."""
    shown: list[Note] = []
    used = 0
    for note in notes:
        cost = len(note.text) + 24
        if used + cost > budget:
            break
        shown.append(note)
        used += cost
    return shown


def _render(
    state: _FileState, edits: dict[int, _Replacement | None], stamp: str
) -> str:
    out: list[str] = []
    cursor = 0
    for index, (start, end) in enumerate(state.blocks):
        out.extend(state.lines[cursor:start])
        cursor = end
        if index not in edits:
            out.extend(state.lines[start:end])
            continue
        replacement = edits[index]
        if replacement is None:
            continue
        out.append(_bullet(replacement.text, replacement.date, replacement.source))
    out.extend(state.lines[cursor:])
    header = dict(state.header)
    header["updated"] = stamp
    return render_front_matter(header) + _squeeze(out)


def _bullet(text: str, date: str, source: str) -> str:
    prefix = f"- [{date}] " if date else "- "
    return f"{prefix}{text} <!-- source: {source} -->"


def _squeeze(lines: list[str]) -> str:
    """Join the lines, collapsing the blank runs the edits leave behind."""
    out: list[str] = []
    blanks = 0
    for line in lines:
        if line.strip():
            blanks = 0
        else:
            blanks += 1
            if blanks > 1:
                continue
        out.append(line)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


def _summary(
    actions: list[_Action],
    run_id: str,
    changed: dict[str, str],
    rejected: int = 0,
) -> str:
    merges = [action for action in actions if action.kind == "merge"]
    retired = [action for action in actions if action.kind == "retire"]
    lines = [
        f"记忆整理完成：合并 {len(merges)} 组重复，删除 {len(retired)} 条记录，"
        f"共 {len(changed)} 个文件有变化。",
    ]
    for action in merges[:5]:
        lines.append(
            "- 合并："
            + " / ".join(_short(note.text, 60) for note in action.notes)
            + " → "
            + _short(action.text, 80)
        )
    if len(merges) > 5:
        lines.append(f"- 另有 {len(merges) - 5} 组合并没有列出")
    for action in retired[:5]:
        lines.append(
            f"- 删除（{action.reason}）：{_short(action.notes[0].text, 80)}"
            + (f" → {_short(action.survivor, 60)}" if action.survivor else "")
        )
    if len(retired) > 5:
        lines.append(f"- 另有 {len(retired) - 5} 条删除没有列出")
    if rejected:
        lines.append(
            f"- 另有 {rejected} 条候选被安全检查拒绝：合并会丢字符，"
            "或删除的理由不在记录里。原来的记录保留。"
        )
    lines.append(
        f"原文件备份在 .state/memory-backups/{run_id}/，任何一条都可以复制回去。"
    )
    return "\n".join(lines)


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1"}


def _as_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fingerprint(cluster: list[Note] | tuple[Note, ...]) -> str:
    """The identity of a candidate set: its members' facts, not their order."""
    keys = sorted(fact_key(note.text) for note in cluster)
    return _hash_text("\n".join(keys))


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


_EPOCH = datetime(1970, 1, 1, tzinfo=ZoneInfo("UTC"))


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
