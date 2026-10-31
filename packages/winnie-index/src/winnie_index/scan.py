"""Walk roots, diff against the catalog, and process what changed.

The sweep is the correctness mechanism: a full metadata walk of ~200k files
costs about a second on this machine, so it runs on a timer and inotify is only
an accelerator. Work is derived from the diff each time, which is why a crashed
run simply resumes on the next sweep.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from winnie_index.chunker import chunk_markdown
from winnie_index.config import Config, Root
from winnie_index.db import set_meta
from winnie_index.decode import BinaryFile, DecodeError, decode_bytes
from winnie_index.tokenize import tokenize

EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "target",
        ".gradle",
        ".cache",
        "__pycache__",
        ".direnv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".idea",
        ".vscode",
    }
)
EXCLUDED_FILE_GLOBS = ("*.min.*", "*.lock", "package-lock.json", "pnpm-lock.yaml")


@dataclass(frozen=True)
class FileEntry:
    rel_path: str
    path: Path
    size: int
    mtime_ns: int
    ino: int
    dev: int


@dataclass
class Stats:
    roots: int = 0
    seen: int = 0
    added: int = 0
    changed: int = 0
    unchanged: int = 0
    renamed: int = 0
    deleted: int = 0
    failed: int = 0
    sealed: int = 0
    chunks: int = 0
    bytes_read: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "roots": self.roots,
            "seen": self.seen,
            "added": self.added,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "renamed": self.renamed,
            "deleted": self.deleted,
            "failed": self.failed,
            "sealed": self.sealed,
            "chunks": self.chunks,
            "bytes_read": self.bytes_read,
        }


def _excluded_dir(relative: str, name: str, root: Root) -> bool:
    if name in EXCLUDED_DIRS:
        return True
    for pattern in root.exclude:
        if fnmatch.fnmatch(relative, pattern):
            return True
    return False


def _wanted(relative: str, name: str, root: Root, prose_ext: frozenset[str]) -> bool:
    if Path(name).suffix.lower().lstrip(".") not in prose_ext:
        return False
    for pattern in EXCLUDED_FILE_GLOBS:
        if fnmatch.fnmatch(name, pattern):
            return False
    for pattern in root.exclude:
        if fnmatch.fnmatch(relative, pattern):
            return False
    if root.include:
        return any(fnmatch.fnmatch(relative, pattern) for pattern in root.include)
    return True


def iter_files(root: Root, prose_ext: frozenset[str]) -> Iterator[FileEntry]:
    """Yield regular files under ``root`` without following symlinks."""
    base = root.path
    stack = [base]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    relative = os.path.relpath(entry.path, base).replace(os.sep, "/")
                    if not _excluded_dir(relative, entry.name, root):
                        stack.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                relative = os.path.relpath(entry.path, base).replace(os.sep, "/")
                if not _wanted(relative, entry.name, root, prose_ext):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            yield FileEntry(
                rel_path=relative,
                path=Path(entry.path),
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                ino=stat.st_ino,
                dev=stat.st_dev,
            )


def _digest(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_root(conn: sqlite3.Connection, root: Root) -> int:
    conn.execute(
        "INSERT INTO roots(name, path, include, exclude, watch) VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET path=excluded.path, include=excluded.include, "
        "exclude=excluded.exclude, watch=excluded.watch",
        (
            root.name,
            str(root.path),
            json.dumps(list(root.include)),
            json.dumps(list(root.exclude)),
            int(root.watch),
        ),
    )
    row = conn.execute("SELECT id FROM roots WHERE name = ?", (root.name,)).fetchone()
    assert row is not None
    return int(row[0])


def _drop_chunks(conn: sqlite3.Connection, doc_id: int) -> None:
    ids = [row[0] for row in conn.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,))]
    if ids:
        conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(chunk_id,) for chunk_id in ids])
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))


def _store_text(conn: sqlite3.Connection, data: bytes) -> tuple[str, str]:
    bytes_hash = _digest(data)
    row = conn.execute(
        "SELECT text FROM contents WHERE bytes_hash = ?", (bytes_hash,)
    ).fetchone()
    if row is not None:
        return bytes_hash, row["text"]
    text, encoding = decode_bytes(data)
    conn.execute(
        "INSERT OR IGNORE INTO contents(bytes_hash, text, encoding) VALUES(?, ?, ?)",
        (bytes_hash, text, encoding),
    )
    return bytes_hash, text


def _index_document(
    conn: sqlite3.Connection, config: Config, doc_id: int, data: bytes, entry: FileEntry
) -> int:
    bytes_hash, text = _store_text(conn, data)
    text_hash = _digest(text.encode("utf-8"))
    row = conn.execute(
        "SELECT bytes_hash, text_hash, state FROM docs WHERE id = ?", (doc_id,)
    ).fetchone()
    assert row is not None
    # The text is unchanged (a line-ending or encoding rewrite): the chunks are
    # still valid, so only the bookkeeping moves.
    if row["state"] == "indexed" and row["text_hash"] == text_hash:
        conn.execute(
            "UPDATE docs SET size=?, mtime_ns=?, ino=?, dev=?, bytes_hash=? WHERE id=?",
            (entry.size, entry.mtime_ns, entry.ino, entry.dev, bytes_hash, doc_id),
        )
        return 0

    chunks, truncated = chunk_markdown(
        text,
        max_tokens=config.chunk_tokens,
        overlap=config.overlap_tokens,
        max_chunks=config.max_chunks,
    )
    _drop_chunks(conn, doc_id)
    for sequence, chunk in enumerate(chunks):
        cursor = conn.execute(
            "INSERT INTO chunks(doc_id, seq, heading, start_line, end_line, text, tokens) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                sequence,
                chunk.heading or None,
                chunk.start_line,
                chunk.end_line,
                chunk.text,
                chunk.tokens,
            ),
        )
        # The heading is part of what a reader sees, so it is searchable; it is
        # stored separately from the body and never duplicated into chunk.text.
        searchable = f"{chunk.heading}\n{chunk.text}" if chunk.heading else chunk.text
        conn.execute(
            "INSERT INTO chunks_fts(rowid, terms) VALUES(?, ?)",
            (cursor.lastrowid, " ".join(tokenize(searchable))),
        )
    note = "chunk cap reached; tail not indexed" if truncated else None
    conn.execute(
        "UPDATE docs SET size=?, mtime_ns=?, ino=?, dev=?, bytes_hash=?, text_hash=?, "
        "state=?, attempts=0, last_error=?, indexed_at=? WHERE id=?",
        (
            entry.size,
            entry.mtime_ns,
            entry.ino,
            entry.dev,
            bytes_hash,
            text_hash,
            "sealed" if truncated else "indexed",
            note,
            _now(),
            doc_id,
        ),
    )
    return len(chunks)


def _insert_doc(conn: sqlite3.Connection, root_id: int, entry: FileEntry) -> int:
    conn.execute(
        "INSERT INTO docs(root_id, rel_path, ext, size, mtime_ns, ino, dev, state) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, 'new')",
        (
            root_id,
            entry.rel_path,
            Path(entry.rel_path).suffix.lower().lstrip("."),
            entry.size,
            entry.mtime_ns,
            entry.ino,
            entry.dev,
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def scan(
    conn: sqlite3.Connection,
    config: Config,
    *,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> Stats:
    """Bring the catalog in line with the roots, processing changed documents."""
    stats = Stats()
    commit_every = 200

    for root in config.roots:
        if not root.path.is_dir():
            stats.errors.append(f"root {root.name}: not a directory: {root.path}")
            continue
        stats.roots += 1
        root_id = 0 if dry_run else _ensure_root(conn, root)

        existing = {}
        if not dry_run:
            existing = {
                row["rel_path"]: row
                for row in conn.execute(
                    "SELECT id, rel_path, size, mtime_ns, ino, dev, bytes_hash, text_hash, state "
                    "FROM docs WHERE root_id = ? AND state != 'deleted'",
                    (root_id,),
                )
            }
        inode_map = {
            (row["dev"], row["ino"]): row for row in existing.values() if row["ino"] and row["dev"]
        }

        entries = list(iter_files(root, config.prose_ext))
        stats.seen += len(entries)
        seen: set[str] = set()
        pending: list[tuple[FileEntry, sqlite3.Row | None]] = []
        processed = 0

        for entry in entries:
            seen.add(entry.rel_path)
            row = existing.get(entry.rel_path)
            if row is None:
                renamed = inode_map.get((entry.dev, entry.ino))
                if renamed is not None and renamed["rel_path"] not in seen:
                    if not dry_run:
                        del existing[renamed["rel_path"]]
                        conn.execute(
                            "UPDATE docs SET rel_path=?, size=?, mtime_ns=? WHERE id=?",
                            (entry.rel_path, entry.size, entry.mtime_ns, renamed["id"]),
                        )
                    stats.renamed += 1
                    continue
                pending.append((entry, None))
                continue
            if (
                row["size"] == entry.size
                and row["mtime_ns"] == entry.mtime_ns
                and row["ino"] == entry.ino
                and row["state"] in ("indexed", "sealed")
            ):
                stats.unchanged += 1
                continue
            pending.append((entry, row))

        for entry, row in pending:
            if dry_run:
                if entry.size > config.max_file_bytes:
                    stats.sealed += 1
                else:
                    stats.added += 1
                continue
            if row is None:
                doc_id = _insert_doc(conn, root_id, entry)
                stats.added += 1
            else:
                doc_id = int(row["id"])
                stats.changed += 1

            if entry.size > config.max_file_bytes:
                conn.execute(
                    "UPDATE docs SET state='sealed', last_error=? WHERE id=?",
                    (f"larger than {config.max_file_bytes // (1024 * 1024)} MB", doc_id),
                )
                stats.sealed += 1
                continue
            try:
                data = entry.path.read_bytes()
                stats.bytes_read += len(data)
                stats.chunks += _index_document(conn, config, doc_id, data, entry)
            except (BinaryFile, DecodeError) as exc:
                conn.execute(
                    "UPDATE docs SET state='sealed', attempts=attempts+1, last_error=? WHERE id=?",
                    (str(exc), doc_id),
                )
                stats.sealed += 1
            except OSError as exc:
                conn.execute(
                    "UPDATE docs SET state='failed', attempts=attempts+1, last_error=? WHERE id=?",
                    (str(exc), doc_id),
                )
                stats.failed += 1
                stats.errors.append(f"{entry.rel_path}: {exc}")
            processed += 1
            if progress and processed % 500 == 0:
                progress(f"{root.name}: {processed}/{len(pending)}")
            if processed % commit_every == 0:
                conn.commit()

        for relative, row in existing.items():
            if relative in seen:
                continue
            if dry_run:
                stats.deleted += 1
                continue
            _drop_chunks(conn, int(row["id"]))
            conn.execute(
                "UPDATE docs SET state='deleted', deleted_at=? WHERE id=?",
                (_now(), row["id"]),
            )
            stats.deleted += 1

        conn.commit()

    if not dry_run:
        set_meta(conn, "last_sweep_at", _now())
        conn.commit()
    return stats
