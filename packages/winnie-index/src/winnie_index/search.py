"""Query the catalog.

Retrieval is BM25 over pre-tokenised terms. Hits are collapsed by text hash so a
file copied into three roots is one result, and capped per document so a long
file cannot fill the answer.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from winnie_index.config import Config
from winnie_index.scan import FileEntry, _index_document
from winnie_index.tokenize import fts_query, raw_terms

_SINCE = re.compile(r"^(\d+)([dhw])$")


def _since_ns(value: str) -> int:
    """Accept an ISO date or a relative window such as ``7d``/``24h``/``2w``."""
    match = _SINCE.match(value.strip().lower())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        delta = {
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }[unit]
        cutoff = datetime.now(timezone.utc) - delta
        return int(cutoff.timestamp() * 1_000_000_000)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"cannot parse since={value!r}; use 7d, 24h or an ISO date") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def _snippet(text: str, terms: list[str], width: int = 200) -> str:
    lowered = text.lower()
    position = -1
    for term in terms:
        found = lowered.find(term)
        if found != -1 and (position == -1 or found < position):
            position = found
    if position == -1:
        position = 0
    start = max(0, position - width // 3)
    end = min(len(text), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].replace("\n", " ").strip() + suffix


def _contains_term(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _candidates(
    conn: sqlite3.Connection,
    config: Config,
    query: str,
    *,
    roots: list[str] | None,
    since: str | None,
    limit: int | None,
) -> tuple[str | None, list[str], list[sqlite3.Row]]:
    """Run the FTS query and return ``(expression, terms, rows)``."""
    match = fts_query(query)
    terms = raw_terms(query)
    if match is None:
        return None, terms, []
    cutoff = _since_ns(since) if since else None
    wanted = max(1, min(limit or config.limit, 50))

    sql = [
        "SELECT c.id AS chunk_id, c.doc_id, c.heading, c.start_line, c.end_line, c.text,",
        "       c.tokens AS tokens, bm25(chunks_fts) AS bm25_score, d.rel_path,",
        "       d.text_hash, d.mtime_ns, r.name AS root",
        "FROM chunks_fts",
        "JOIN chunks c ON c.id = chunks_fts.rowid",
        "JOIN docs d ON d.id = c.doc_id",
        "JOIN roots r ON r.id = d.root_id",
        "WHERE chunks_fts MATCH ? AND d.state = 'indexed'",
    ]
    params: list[object] = [match]
    if roots:
        sql.append("AND r.name IN (" + ",".join("?" for _ in roots) + ")")
        params.extend(roots)
    if cutoff is not None:
        sql.append("AND d.mtime_ns >= ?")
        params.append(cutoff)
    sql.append("ORDER BY bm25_score LIMIT ?")
    params.append(max(config.bm25_k, wanted * 4))

    return match, terms, conn.execute("\n".join(sql), params).fetchall()


def search(
    conn: sqlite3.Connection,
    config: Config,
    query: str,
    *,
    roots: list[str] | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    match, terms, rows = _candidates(
        conn, config, query, roots=roots, since=since, limit=limit
    )
    if match is None:
        return []
    wanted = max(1, min(limit or config.limit, 50))

    owners: dict[str, dict[str, object]] = {}
    per_doc: dict[int, int] = {}
    results: list[dict[str, object]] = []
    for row in rows:
        haystack = f"{row['heading'] or ''}\n{row['text']}"
        if not _contains_term(haystack, terms):
            continue
        doc_id = int(row["doc_id"])
        if per_doc.get(doc_id, 0) >= config.max_per_doc:
            continue
        text_hash = row["text_hash"] or f"chunk:{row['chunk_id']}"
        owner = owners.get(text_hash)
        if owner is not None and int(owner["_doc_id"]) != doc_id:
            # Another path holds the same text: one result, with an alias list.
            also = owner.setdefault("also_at", [])
            assert isinstance(also, list)
            if row["rel_path"] not in also:
                also.append(row["rel_path"])
            continue
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        hit = {
            "path": row["rel_path"],
            "root": row["root"],
            "heading": row["heading"] or "",
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "snippet": _snippet(row["text"], terms),
            "score": row["bm25_score"],
            "mtime": datetime.fromtimestamp(
                row["mtime_ns"] / 1_000_000_000, tz=timezone.utc
            ).isoformat(timespec="seconds"),
            "text_hash": text_hash,
            "_doc_id": doc_id,
        }
        owners[text_hash] = hit
        results.append(hit)
        if len(results) >= wanted:
            break

    for hit in results:
        hit.setdefault("also_at", [])
        hit.pop("_doc_id", None)
    return results


def _debug_hint(
    match: str | None, terms: list[str], candidates: int, dropped: int, returned: int
) -> str:
    if match is None:
        return (
            "The query has no indexable terms: use letters or at least two "
            "Chinese characters."
        )
    if candidates == 0:
        return (
            "No chunk matched the FTS expression. Try fewer or different words, "
            "or run a build if the files are new."
        )
    if returned == 0 and dropped:
        return (
            "Every candidate failed the term-coverage check, which usually means "
            "the index is behind the files: run a build."
        )
    return ""


def search_debug(
    conn: sqlite3.Connection,
    config: Config,
    query: str,
    *,
    roots: list[str] | None = None,
    since: str | None = None,
    limit: int | None = None,
    text_chars: int = 600,
) -> dict[str, object]:
    """Return chunk-level hits plus the reasoning, for the console's test panel.

    This is deliberately not the retrieval contract: it shows every matching
    chunk instead of collapsing duplicates and capping per document, and it
    returns the FTS expression so a zero-result search can explain itself.
    """
    started = time.perf_counter()
    match, terms, rows = _candidates(
        conn, config, query, roots=roots, since=since, limit=limit
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    hits: list[dict[str, object]] = []
    dropped = 0
    for row in rows:
        haystack = f"{row['heading'] or ''}\n{row['text']}"
        if not _contains_term(haystack, terms):
            dropped += 1
            continue
        hits.append(
            {
                "chunk_id": int(row["chunk_id"]),
                "doc_id": int(row["doc_id"]),
                "root": row["root"],
                "path": row["rel_path"],
                "heading": row["heading"] or "",
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "score": round(float(row["bm25_score"]), 4),
                "tokens": int(row["tokens"] or 0),
                "mtime": datetime.fromtimestamp(
                    row["mtime_ns"] / 1_000_000_000, tz=timezone.utc
                ).isoformat(timespec="seconds"),
                "text_hash": row["text_hash"],
                "text": row["text"][:text_chars],
                "truncated": len(row["text"]) > text_chars,
            }
        )

    files: dict[str, dict[str, object]] = {}
    for hit in hits:
        key = f"{hit['root']}:{hit['path']}"
        entry = files.get(key)
        if entry is None:
            files[key] = {
                "root": hit["root"],
                "path": hit["path"],
                "chunks": 1,
                "best_score": hit["score"],
                "heading": hit["heading"],
                "start_line": hit["start_line"],
                "end_line": hit["end_line"],
                "chunk_ids": [hit["chunk_id"]],
            }
            continue
        entry["chunks"] = int(entry["chunks"]) + 1
        assert isinstance(entry["chunk_ids"], list)
        entry["chunk_ids"].append(hit["chunk_id"])
        if float(hit["score"]) < float(entry["best_score"]):
            entry["best_score"] = hit["score"]
            entry["heading"] = hit["heading"]
            entry["start_line"] = hit["start_line"]
            entry["end_line"] = hit["end_line"]

    return {
        "query": query,
        "expression": match,
        "terms": terms,
        "roots": roots or [],
        "elapsed_ms": elapsed_ms,
        "candidates": len(rows),
        "returned": len(hits),
        "dropped": dropped,
        "hint": _debug_hint(match, terms, len(rows), dropped, len(hits)),
        "hits": hits,
        "files": sorted(files.values(), key=lambda item: float(item["best_score"])),
    }


def resolve_document(
    conn: sqlite3.Connection, roots: list[str] | None, path: str
) -> tuple[int, str, int]:
    """Return ``(doc_id, rel_path, root_id)`` for a user-supplied path."""
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        best = None
        for row in conn.execute(
            "SELECT docs.id AS id, docs.root_id AS root_id, docs.rel_path AS rel_path, "
            "roots.path AS path FROM docs JOIN roots ON roots.id = docs.root_id"
        ):
            root_path = Path(row["path"])
            try:
                relative = candidate.relative_to(root_path)
            except ValueError:
                continue
            if best is None or len(str(root_path)) > best[0]:
                best = (len(str(root_path)), int(row["id"]), str(relative), int(row["root_id"]))
        if best is None:
            raise LookupError(f"not under any configured root: {path}")
        return best[1], best[2], best[3]
    if roots:
        for name in roots:
            row = conn.execute(
                "SELECT docs.id, docs.rel_path, docs.root_id FROM docs "
                "JOIN roots ON roots.id = docs.root_id WHERE roots.name = ? AND docs.rel_path = ?",
                (name, path.lstrip("./")),
            ).fetchone()
            if row:
                return int(row[0]), row[1], int(row[2])
    row = conn.execute(
        "SELECT id, rel_path, root_id FROM docs WHERE rel_path = ? AND state != 'deleted'",
        (path.lstrip("./"),),
    ).fetchone()
    if row is None:
        raise LookupError(f"no indexed document at {path}")
    return int(row[0]), row[1], int(row[2])


def get_document(
    conn: sqlite3.Connection,
    config: Config,
    path: str,
    *,
    roots: list[str] | None = None,
    from_line: int = 1,
    lines: int | None = None,
) -> dict[str, object]:
    """Return document text, revalidated against the file before it is emitted."""
    doc_id, rel_path, root_id = resolve_document(conn, roots, path)
    root = conn.execute("SELECT name, path FROM roots WHERE id = ?", (root_id,)).fetchone()
    assert root is not None
    absolute = Path(root["path"]) / rel_path

    doc = conn.execute("SELECT * FROM docs WHERE id = ?", (doc_id,)).fetchone()
    assert doc is not None

    fresh = True
    try:
        stat = absolute.stat()
        changed = (
            stat.st_size != doc["size"]
            or stat.st_mtime_ns != doc["mtime_ns"]
            or stat.st_ino != doc["ino"]
        )
        if changed:
            fresh = False
            entry = FileEntry(
                rel_path=rel_path,
                path=absolute,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                ino=stat.st_ino,
                dev=stat.st_dev,
            )
            data = absolute.read_bytes()
            _index_document(conn, config, doc_id, data, entry)
            conn.commit()
    except OSError as exc:
        raise LookupError(f"cannot read {absolute}: {exc}") from exc

    doc = conn.execute("SELECT bytes_hash FROM docs WHERE id = ?", (doc_id,)).fetchone()
    text = ""
    if doc["bytes_hash"]:
        content = conn.execute(
            "SELECT text FROM contents WHERE bytes_hash = ?", (doc["bytes_hash"],)
        ).fetchone()
        text = content["text"] if content else ""
    total_lines = text.count("\n") + (1 if text else 0)
    start = max(1, from_line)
    slice_lines = text.split("\n")
    if start > len(slice_lines):
        body: list[str] = []
    else:
        end = len(slice_lines) if lines is None else start - 1 + lines
        body = slice_lines[start - 1 : end]

    return {
        "path": rel_path,
        "root": root["name"],
        "absolute_path": str(absolute),
        "from_line": start,
        "total_lines": total_lines,
        "fresh": fresh,
        "text": "\n".join(body),
    }


def status(conn: sqlite3.Connection, config: Config) -> dict[str, object]:
    counts = {
        row["state"]: row["count"]
        for row in conn.execute("SELECT state, COUNT(*) AS count FROM docs GROUP BY state")
    }
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    roots = [
        {
            "name": row["name"],
            "path": row["path"],
            "documents": row["documents"],
            "chunks": row["chunks"],
        }
        for row in conn.execute(
            "SELECT r.name, r.path, COUNT(d.id) AS documents, "
            "COALESCE(SUM((SELECT COUNT(*) FROM chunks c WHERE c.doc_id = d.id)), 0) AS chunks "
            "FROM roots r LEFT JOIN docs d ON d.root_id = r.id AND d.state = 'indexed' "
            "GROUP BY r.id ORDER BY r.name"
        )
    ]
    sweep = conn.execute("SELECT value FROM meta WHERE key = 'last_sweep_at'").fetchone()
    failures = [
        {"path": row["rel_path"], "error": row["last_error"]}
        for row in conn.execute(
            "SELECT rel_path, last_error FROM docs WHERE state = 'failed' ORDER BY id LIMIT 5"
        )
    ]
    db_bytes = config.db_path.stat().st_size if config.db_path.exists() else 0
    return {
        "documents": counts,
        "chunks": chunks,
        "roots": roots,
        "last_sweep_at": sweep[0] if sweep else None,
        "failures": failures,
        "db_bytes": db_bytes,
        "db_path": str(config.db_path),
    }
