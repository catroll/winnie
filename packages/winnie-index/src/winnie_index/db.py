"""SQLite schema and connection helpers for the local index.

The catalog is the only stateful part of the system: documents, their decoded
text, their chunks and the full-text index all live in one file, so a query can
never see a chunk whose document was already removed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roots (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  path TEXT NOT NULL,
  include TEXT NOT NULL DEFAULT '[]',
  exclude TEXT NOT NULL DEFAULT '[]',
  watch INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS docs (
  id INTEGER PRIMARY KEY,
  root_id INTEGER NOT NULL REFERENCES roots(id),
  rel_path TEXT NOT NULL,
  ext TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  ino INTEGER NOT NULL,
  dev INTEGER NOT NULL,
  bytes_hash TEXT,
  text_hash TEXT,
  state TEXT NOT NULL DEFAULT 'new',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  indexed_at TEXT,
  deleted_at TEXT,
  UNIQUE(root_id, rel_path)
);
CREATE INDEX IF NOT EXISTS docs_text_hash ON docs(text_hash);
CREATE INDEX IF NOT EXISTS docs_state ON docs(state);

-- Decoded text is keyed by content: a rename or a copy never decodes twice.
CREATE TABLE IF NOT EXISTS contents (
  bytes_hash TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  encoding TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  doc_id INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  heading TEXT,
  start_line INTEGER,
  end_line INTEGER,
  text TEXT NOT NULL,
  tokens INTEGER NOT NULL,
  UNIQUE(doc_id, seq)
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);

-- The indexed column holds pre-tokenised terms (CJK bigrams + ASCII words).
-- rowid equals chunks.id, so a hit joins straight back to its source row.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  terms, tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class SchemaError(RuntimeError):
    """The catalog was created by a newer or incompatible version."""


def connect(path: Path) -> sqlite3.Connection:
    """Open (and if needed create) the catalog."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise SchemaError(
            f"catalog schema v{version} is newer than this build (v{SCHEMA_VERSION})"
        )
    if version == 0:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row[0]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
