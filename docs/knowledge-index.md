# Local document index — technical design

**Status:** v1 (lexical) implemented in `packages/winnie-index`; dense retrieval,
the watcher and the reranker are still design. See §17 for what exists today.

**Scope, v1 (2026-11-01):** `.txt` and `.md` only. Roots are configured later.
No sensitive-root handling in v1 — exclusions are a config concern. Code, PDF,
Office, OCR and e-mail are deferred. The server is always on and runs as a
`systemctl --user` service. Clients reach it through MCP (loopback HTTP, plus a
stdio shim) and HTTP; the Agent Skill only declares those two surfaces.

**Goal.** One local index over the owner's text files, kept in step with the
filesystem, that any agent can query through a tool it already understands, with
every answer traceable to a file and a line range.

**Non-goals.** Cloud sync, multi-user access control, a web UI, a write path into
the indexed files, and replacing the assistant's long-term memory: memory holds
distilled facts about the owner, this index holds the source text those facts can
be checked against.

## 1. What the index must do

| Requirement | Consequence |
|---|---|
| v1: `.txt` + `.md`, ~19k files, roots added later | Roots are configuration; the index lives outside the checkout |
| Files change often | Incremental, content-addressed ingestion; never re-read everything per change |
| "Always the latest version" | Bounded staleness per surface plus read-time revalidation (§3) |
| Agents must find it unprompted | A Skill that declares MCP/HTTP, nothing else (§8.1) |
| Winnie must use it too | HTTP API plus one assistant tool, separate from memory |
| One user, one machine, always on | One daemon under `systemctl --user`; no Elasticsearch, no cluster |

"Always latest" is not free: a derived index only knows what a scan or an event
told it. This design states the window per guarantee and closes the rest at read
time instead of pretending it does not exist.

## 2. Measured corpus (this host, 2026-11-01)

| Measurement | Value |
|---|---|
| v1 candidates: files ending `.txt` or `.md`, ≤ 2 MB, caches excluded | **19,189 files / 180.4 MB** |
| Unique content (SHA-1 over every candidate, counted once) | **18,111 files** — 1,078 byte-identical duplicates (5.6 %) |
| Wider text-only set (adds code + config, deferred) | 44,943 files / 403.5 MB |
| Metadata scan of 202,798 files | 1.0 s warm (one `EACCES` on a root-owned `postgres` data dir) |
| Hashing throughput | 403 MB in 7.3 s, including one process per 100 files |
| Directories under `~/Projects` alone | 104,762 (inotify limit 1,048,576) |
| Free disk | 68 GB (93 % used) |
| Cores / RAM / GPU | 16 / 26 GB / none |
| SQLite | 3.46.1, FTS5 available, ICU tokenizer absent |
| Python | 3.14.4 system, 3.13 present, `uv` absent |
| Node.js | 22.22.1 (only relevant to the qmd options, §13) |

Estimated for v1: **120–160k chunks**, index **0.8–1.5 GB**, one-off embedding
**1–1.5 hours** on CPU. The phase-0 dry run replaces every estimate with a count.

One finding still shapes the rules: extension is not proof of content. Under a
text allowlist, 1,345 files ending in `.ts` turned out to be 6.9 GB of MPEG
transport-stream segments. A size cap and a decode gate (§6.2) catch whatever a
future directory invents.

## 3. The freshness contract

| Level | Guarantee | Mechanism |
|---|---|---|
| Read | A document handed to a model is never stale | `get` stats the file; on any difference it re-reads and re-chunks before returning text |
| Search | Index reflects changes within one sweep (default 300 s); ~2 s where the watcher is active | sweep diff plus debounced inotify events |
| Consistency | No ghosts, no duplicated documents | tombstones on delete, content-hash dedupe, daily reconciliation |

Three-stage change detection keeps "often modified" cheap:

```
stat (size, mtime_ns, ino)  ──unchanged──►  nothing
        │ changed
        ▼
bytes hash (blake2b)        ──unchanged──►  stat-only update
        │ changed
        ▼
text hash                   ──unchanged──►  metadata update, no re-chunk
        │ changed
        ▼
re-chunk (+ re-embed that document only)
```

- An editor that rewrites a file, or a sync client that touches mtime, costs one
  hash — not a re-chunk and not an embedding.
- Rename/move is matched by `(dev, ino)` first, then `(size, mtime_ns, hash
  prefix)`; the path changes, chunks and vectors do not.
- Delete writes a tombstone, purges chunks and vectors asynchronously, and keeps
  the row for 30 days so an accidental delete is recoverable.
- Daily reconciliation re-hashes a 1 % sample and every file whose mtime is in
  the future or predates its ctime, then compares row/chunk/vector counts.

The watcher is an accelerator, never the correctness mechanism: on overflow, a
killed process or a network mount, the next sweep still converges. It is opt-in
per root because `~/Projects` alone has 104k directories.

## 4. Architecture

```
roots (YAML) ──► scanner ──► diff vs catalog ──► jobs (SQLite)
                    ▲                                 │
        inotify (optional, per root)                  ▼
                                            decode → chunk → dedupe
                                                      │
                              ┌───────────────────────┴────────────┐
                              ▼                                    ▼
                    FTS5 (CJK bigram terms)              sqlite-vec (dense)
                              └───────────────┬────────────────────┘
                                              ▼
                                    BM25 + KNN → RRF
                                              │
                     ┌────────────────────────┼────────────────────────┐
                     ▼                        ▼                        ▼
              MCP (HTTP)              MCP (stdio shim)            HTTP /search
                     └─────────── Skill declares these ───────────┘
```

One daemon owns the database, the scanner, the queue and both indexes. One
process means no cross-process locking and no second service: a query can never
see chunks the catalog has already deleted.

```
~/.local/share/winnie-index/
  catalog.sqlite        # docs, chunks, FTS5, vectors, jobs (WAL)
  index.log             # JSON lines
  backups/              # 2 daily sqlite .backup snapshots
```

### 4.1 Configuration

One YAML file; the values below are the built-in defaults, not required keys.
Precedence is `defaults < YAML < WINNIE_INDEX_* environment < flags`, unknown
keys are rejected at startup, and a missing file at the default path is fine
while a missing file passed with `--config` is an error.

```yaml
# ~/.config/winnie-index/config.yaml
data_dir: ~/.local/share/winnie-index

# Roots are added later; nothing is indexed until at least one exists.
roots:
  - name: notes
    path: /home/catroll/Notes
    tier: dense
    exclude: []
    watch: false

formats:
  prose:  [txt, md]            # v1
  # code:   [py, go, js, ts]   # deferred
  # config: [yaml, json]       # deferred

scan:
  interval_seconds: 300
  watch: false
  debounce_ms: 1500

index:
  max_file_mb: 2
  max_chunks_per_doc: 2000
  chunk_tokens: 500
  overlap_tokens: 60
  vectors: sqlite-vec          # sqlite-vec | qdrant
  qdrant_url: http://127.0.0.1:6333
  dense:
    enabled: true
    model: BAAI/bge-m3
    dims: 1024
    batch: 16

retrieval:
  bm25_k: 50
  dense_k: 50
  rrf_k: 60
  max_per_doc: 2
  limit: 8
  min_score: 0.25              # below this, return nothing

serve:
  http: 127.0.0.1:8787         # /search /doc /status /healthz and /mcp
  mcp: http                    # http | stdio
  token_env: WINNIE_INDEX_TOKEN  # optional; empty means loopback trust

skill:
  enabled: true
  target: ~/.agents/skills/local-knowledge
```

## 5. Data model

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE roots (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
  tier TEXT NOT NULL DEFAULT 'dense',     -- dense | lexical
  include TEXT NOT NULL, exclude TEXT NOT NULL,   -- JSON arrays of globs
  enabled INTEGER NOT NULL DEFAULT 1, watch INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE docs (
  id INTEGER PRIMARY KEY,
  root_id INTEGER NOT NULL REFERENCES roots(id),
  rel_path TEXT NOT NULL, ext TEXT NOT NULL, kind TEXT NOT NULL, -- prose|code|config
  size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, ino INTEGER NOT NULL,
  dev INTEGER NOT NULL,
  bytes_hash TEXT,             -- blake2b of raw bytes; NULL until hashed
  text_hash TEXT,              -- blake2b of normalised text
  state TEXT NOT NULL,         -- new|indexed|sealed|failed|deleted
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  indexed_at TEXT, deleted_at TEXT,
  UNIQUE(root_id, rel_path)
);

-- Decoded text is keyed by content, so a rename or a copy never pays twice.
CREATE TABLE contents (
  bytes_hash TEXT PRIMARY KEY, text TEXT NOT NULL,
  encoding TEXT NOT NULL, meta TEXT NOT NULL   -- JSON: lines, warnings, sniff
);

CREATE TABLE chunks (
  id INTEGER PRIMARY KEY,
  doc_id INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  heading TEXT,                -- Markdown heading path, e.g. `Deploy > Rollback`
  start_line INTEGER, end_line INTEGER,
  text TEXT NOT NULL,
  terms TEXT NOT NULL,         -- pre-tokenised: CJK bigrams + ASCII words
  tokens INTEGER NOT NULL,
  UNIQUE(doc_id, seq)
);

-- Not external-content: rowid equals chunks.id and the indexed column holds
-- pre-tokenised terms, so a hit joins straight back to its source row.
CREATE VIRTUAL TABLE chunks_fts USING fts5(terms, tokenize='unicode61');

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version, tokenizer, embedding_model, embedding_dims, last_sweep_at
```

Two tables from the earlier design are deliberately absent in v1. There is no
`jobs` table: a sweep derives its work from the diff, so a crash simply resumes
at the next sweep, and the queue only earns its keep once embedding makes work
long enough to schedule. There is no vector table yet; `embedding_model` and
`embedding_dims` will be added to `meta` with it, because changing the model
without a full re-embed leaves two vector spaces in one table and presents as a
mysterious quality regression.

## 6. Ingestion

### 6.1 Scope rules

Evaluated in order, all configurable per root:

1. root `include` / `exclude` globs;
2. extension allowlist — v1 is `txt` and `md`;
3. per-file cap 2 MB (`sealed` above it, counted, not failed);
4. excluded by default: `.git`, `node_modules`, `.venv`, `dist`, `build`,
   `target`, `data/cache`, hidden dirs, `*.min.*`, lock files;
5. symlinks not followed; each root walked with `xdev` so a mount cannot pull in
   200 GB silently.

### 6.2 Decoding gate

No parser zoo, because there is nothing to parse — but "it ends in `.md`" is not
proof that it is text:

1. read at most 2 MB;
2. refuse a NUL byte in the first 8 KB (`sealed: binary`);
3. decode as UTF-8, then UTF-8-SIG, then GB18030; failure seals the file with the
   attempted encodings in `last_error`;
4. normalise CRLF to LF and strip the BOM, but **never** strip leading
   whitespace;
5. store the text under `bytes_hash` and record the encoding used.

GB18030 is tried last rather than `latin-1`, because a mis-decoded Chinese file
becomes mojibake that embeds, ranks and is cited. A file that cannot be decoded
is visibly sealed, not silently garbled.

### 6.3 Chunking

| Kind | Boundary | Header |
|---|---|---|
| prose (v1) | Markdown headings, then paragraphs; 500 tokens, 60 overlap | ancestor heading path |
| code (deferred) | top-level symbol boundaries via tree-sitter, else blank-line blocks | `path :: symbol` |
| config (deferred) | line blocks | `path` |

- Keep `start_line`/`end_line` so a citation is actionable and the model can read
  exactly the cited region.
- Never split a fenced code block that fits; if one exceeds the cap, keep it
  whole and mark it truncated.
- Per-document cap of 2,000 chunks so one generated file cannot dominate.

### 6.4 Dedupe

- **Exact:** `contents` keyed by `bytes_hash`; identical bytes share one decode
  and one set of chunks, while `docs` keeps every path.
- **Near:** `text_hash` after line-ending normalisation. Search collapses hits
  sharing a `text_hash` into one result whose `also_at` lists the other paths.
  In the v1 subset that is 1,078 files that would otherwise be duplicate hits.

## 7. Indexing and retrieval

### 7.1 Lexical

**CJK bigrams, pre-tokenised, indexed by FTS5 `unicode61`.** This mirrors
`winnie_assistant.memory.tokenize`, which already made the same decision for
memory, so the project keeps one tokenisation rule instead of two.

Tested on this host, not assumed:

- `tokenize='trigram'` matches `今天天气` but **not** `天气`: trigram needs three
  characters, and two-character Chinese words are the common case.
- `tokenize='icu'` is absent from this SQLite build (`no such tokenizer: icu`).
- Pre-tokenised bigrams: `天气`, `公园`, `散步` and the phrase `今天天气` all
  match; ASCII words are unaffected; no new dependency.

Queries are tokenised by the same function. A segment with more than one term
becomes an FTS5 phrase (`"今天 天天 天气"`); segments are joined with AND, so a
multi-word query does not degrade into a bag of bigrams. Cost: the `terms`
column roughly doubles token volume, measured in phase 1.

### 7.2 Dense

| Decision | Choice | Reason |
|---|---|---|
| Store | `sqlite-vec` in the catalog file | ~160k × 1024 × 4 B ≈ 650 MB f32, ~165 MB int8; brute-force KNN is ~40–120 ms and transactional with the rows it points at |
| Scale-out | Qdrant behind a `VectorStore` port | Trigger: p95 > 500 ms, > 500k vectors, or a second reader host |
| Model | multilingual, 1024-d, CPU-feasible — `BAAI/bge-m3` as candidate | Chinese-heavy corpus; no GPU; ONNX/GGUF availability verified before adoption |
| Build | one-off batch, resumable per document | ~150k chunks at 30–60 chunks/s is 1–1.5 hours, then incremental |
| Status | not implemented in v1 | The sessions shipped so far are BM25-only; §17 lists the commands that exist |

Privacy default: local model, no vendor API. An API embedding provider may be
added behind a flag that prints a warning, because shipping a personal corpus to
a vendor is a different product, not a configuration detail.

### 7.3 Fusion

```
BM25 top 50  ─┐
              ├─► RRF (k=60) ─► per-doc cap (2 chunks) ─► top 8 ─► optional rerank
dense top 50 ─┘
```

- RRF, not score normalisation: BM25 and cosine are not comparable, and a
  [50k-document comparison](https://builder.aws.com/content/3KKg66VgAc6DKoYg8ubOBiIoI49)
  puts hybrid above either backend alone.
- A per-document cap stops one long file from filling every slot.
- Reranking is phase 4, only if the fixture says hybrid is not enough.
- A relevance floor is mandatory: below `min_score`, return nothing and say so.
- Every hit carries `path`, `heading`, `start_line`–`end_line`, `mtime`,
  `content_hash`, `score`, a snippet, and `fresh` (false means the file changed
  after indexing and the text was re-read at query time).

## 8. Delivery surfaces

### 8.1 Agent Skill

The skill is a **declaration only**: name, description, and the two ways to reach
the server. It does not teach the model which directories exist, how to grep, or
what to run — that is the index's job, behind a tool.

```
~/.agents/skills/local-knowledge/SKILL.md     # ~40 lines, the only file
```

```markdown
---
name: local-knowledge
description: Search the owner's local notes and Markdown documents. Use when a
  question depends on what the owner wrote or saved before ("我的笔记",
  "之前写的", "找一下那份文档", "where did I", "what did we decide about").
  Backed by an MCP server named local-knowledge and an HTTP API on
  127.0.0.1:8787; do not search the filesystem for these documents.
---

# Local knowledge

An index of the owner's own `.txt` and `.md` files. It is reached through tool
calls, never by walking directories.

## Tools
- `search_documents(query, roots?, since?, limit?)` → ranked hits with path,
  heading, line range, mtime and score.
- `get_document(path, from_line?, lines?)` → the cited text, revalidated against
  the file before it is returned.
- `index_status()` → coverage, last sweep, failures.

## HTTP (when MCP is unavailable)
`POST http://127.0.0.1:8787/search` with `{"query": "...", "limit": 8}`.

## Rules
- Answer only from text returned by `get_document`; cite `path:line`.
- An empty result means the corpus has nothing; say so instead of guessing.
- Results carry `mtime`; re-run the search after files change.
```

Both client families discover this directory without a symlink: Cursor 3.x loads
`.agents/skills/` and `~/.agents/skills/` (plus the Claude/Codex directories),
and DSH's filesystem provider scans `<agentsHome>/skills` and watches it, so a
regenerated skill reaches the next session without a restart.

Registration is per client, because a skill declares a capability but does not
configure the transport:

| Client | Registration |
|---|---|
| DSH | MCP server entry in the DSH config overlay (exact key confirmed in P3) |
| Cursor | `.cursor/mcp.json` with `{"mcpServers": {"local-knowledge": {"url": "http://127.0.0.1:8787/mcp"}}}` |
| Claude Code | `claude mcp add --transport http local-knowledge http://127.0.0.1:8787/mcp` |

Registration syntax is confirmed in P3; the table states the intended shape.

The skill never contains document text, path lists or query recipes: it is
context that every agent pays for on every turn.

### 8.2 MCP

| Tool | Arguments | Returns |
|---|---|---|
| `search_documents` | `query`, `roots?`, `since?`, `limit?` | ranked hits with citation fields; an empty list is a valid answer |
| `get_document` | `path`, `from_line?`, `lines?` | revalidated text with line numbers |
| `index_status` | — | counts by state, last sweep, lag, failures, disk, model |

Transport: loopback HTTP (`POST /mcp`, streamable, stateless) as the default, plus
a `stdio` shim that proxies to the running daemon. HTTP is preferred because the
daemon already holds the index and model; stdio exists for clients that cannot
reach a port. Tool descriptions are written for selection, not documentation.

### 8.3 HTTP

`POST /search`, `GET /doc/{id}`, `GET /status`, `GET /healthz`. Loopback only; a
non-loopback bind requires `WINNIE_INDEX_TOKEN` and warns at startup. This is what
Winnie's assistant calls.

### 8.4 Winnie

One assistant tool, `search_documents`, modelled on the existing `memory_search`
contract: plain text, bounded, `path:line` citations, empty when nothing matches.
Memory and documents remain separate tools — memory is what the assistant was
told, the index is where the source is.

### 8.5 Admin console

The console is the fourth surface and the only one that writes. It reaches the
index through the Winnie API rather than directly, so the browser keeps one
origin and one authenticated session:

```
browser ──/api/v1/index/*──► Winnie API ──proxy──► index daemon /admin/*
```

| Endpoint | Purpose |
|---|---|
| `GET /admin/status` | Coverage, roots, failures, endpoints, skill state |
| `GET /admin/config` | Editable configuration with roots normalised |
| `PUT /admin/config` | Validate, write the YAML, reload in place |
| `POST /admin/build` | `dry_run` preview or a real sweep, under a build lock |
| `POST /admin/skill` | Install or check the Agent Skill |

Rules that make writing safe:

- **The file stays the source of truth.** The daemon validates the merged YAML,
  writes it atomically (`0600`, temp file + rename), then reloads the same way a
  restart would. A rejected save touches neither the file nor the running config.
- **Only editable keys.** Roots, formats, scan, index, retrieval and skill can be
  changed; `data_dir` and `serve.http` cannot, because moving the catalog or the
  listen address from a web form is a foot-gun. A payload that changes either is
  rejected before the write.
- **Removing a root retires its documents.** The rows and chunks go; the files on
  disk are untouched, so re-adding the root rebuilds them.
- **Bounds are enforced server-side,** not just in the form: sweep interval
  5–86400 s, file cap 1–512 MB, results 1–50, chunks per document 1–10.
- **A page on another origin cannot drive it.** `/admin/*` rejects a browser
  request whose `Origin` is not loopback, on top of the loopback bind.
- **Saving rewrites the file and drops comments.** The file carries a header
  saying so; a hand-edited file stays valid, it just loses its comments on the
  next save.

## 9. Operations

| Concern | Decision |
|---|---|
| Process | `systemctl --user` unit, `Restart=on-failure`; `loginctl enable-linger` (currently `Linger=no`) so it survives logout; sweep on start, then every 300 s |
| Existing pattern | the machine already runs `dsh-web.service` under the same manager, so this matches local practice |
| Initial build | `winnie-index build --dry-run` prints counts and bytes per root/format and writes nothing; the real build is resumable and safe to interrupt |
| Order | lexical for all roots first (usable in minutes), dense in the background |
| Backpressure | the scanner pauses when pending jobs exceed the threshold; `status` shows queue depth |
| Disk | budget 4 GB (index 0.8–1.5 GB, scratch, 2 snapshots); warn below 30 GB free, refuse to embed below 15 GB (68 GB today) |
| Growth | daily `PRAGMA optimize`, weekly `VACUUM` if freed pages > 10 %, 2 daily `sqlite3 .backup` snapshots (the index is rebuildable; snapshots are convenience) |
| Observability | JSON lines per sweep, batch and failure; `/healthz` returns queue depth and last-sweep age |
| Doctor | `winnie-index doctor` checks sqlite-vec load, FTS5, model dims, watch limits, free space, skill drift |

```
winnie-index init | scan | build [--dry-run] | embed | search | get
             status | doctor | skill install --check | mcp [--http|--stdio] | gc
```

## 10. Security and privacy

- Local only; no network egress in the default configuration.
- Catalog mode `0600`, directory `0700`.
- HTTP/MCP bind `127.0.0.1`; non-loopback requires a token.
- v1 has no sensitive-root concept; exclusions are ordinary `exclude` globs, and
  a `sensitive` flag is added only when there is a root that needs it.
- Logs record paths, sizes, hashes and errors — never document text.
- `get_document` refuses paths outside configured roots, so a crafted path cannot
  read `~/.ssh`.

## 11. Evaluation

A fixture of 40 queries with expected files, written before tuning:

| Metric | Target |
|---|---|
| recall@5, lexical only | ≥ 0.75 |
| recall@5, hybrid | ≥ 0.85 |
| MRR@10, hybrid | ≥ 0.60 |
| Empty-result accuracy (10 queries with no answer) | ≥ 0.80 |
| p95 search latency | < 300 ms |
| Index lag, watcher on / off | < 5 s / ≤ sweep interval |
| `get` after an edit | always current |

Behavioural tests, cheap and decisive:

1. copy a file into a second root → one result, two paths;
2. rename a file → same chunks, new path, zero re-embeddings;
3. touch without content change → no decode, no embedding;
4. delete → gone within one sweep, tombstone present;
5. append a paragraph → found within one sweep;
6. ask an agent "我之前写的关于 X 的笔记" with no path given → the skill is
   selected, `search_documents` is called, the answer cites `path:line`.

Test 6 decides whether the skill is doing its job, and is the one most easily
skipped.

## 12. Phases (self-built option)

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 Inventory** | `build --dry-run`, scope rules, per-root report; the option choice in §13 | counts match §2 within 5 %; nothing written |
| **P1 Lexical + Skill** | catalog, sweep, decode gate, chunker, FTS5, `search`/`get` CLI, skill install | 19k files indexed; p95 < 200 ms; rename/delete/touch tests pass; an agent answers from the skill with no path given |

P1 and the MCP/HTTP surface are implemented; P0 (the dry run) exists as
`build --dry-run`, P2–P4 are open.
| **P2 Dense** | embeddings, sqlite-vec, RRF | hybrid recall@5 ≥ lexical + 0.1; build time and disk recorded |
| **P3 MCP + Winnie** | MCP HTTP/stdio, HTTP API, assistant tool | one Winnie answer cites an indexed file; a Cursor session lists the tools |
| **P4 Operations** | watcher, systemd unit, linger, gc, doctor | 7 days unattended; lag within target |

Each phase ends with a commit carrying its measured numbers, so later tuning has
a baseline to beat.

## 13. Implementation options: self-built vs qmd

Three ways to get the same external contract (MCP HTTP + stdio, HTTP, a thin
skill). They differ in who owns the index and how Chinese text behaves.

**A. qmd as-is.** `qmd collection add` for each root, a timer running
`qmd update`, `qmd mcp --http`, and our 40-line `SKILL.md`. qmd 2.8.3 (MIT,
published 2026-08-16) already provides SQLite FTS5 + sqlite-vec, RRF fusion, an
LLM reranker, an MCP server with `query`/`get`/`multi_get`/`status`/`metadata`,
and an HTTP search endpoint.

**B. qmd SDK.** Import `createStore()` from `@tobilu/qmd` in a small Node/TS
daemon, own the HTTP and MCP surface, add read-time revalidation and our own tool
schemas. Reuses qmd's ingestion, hybrid retrieval and reranking; still Node.

**C. Self-built.** The design in this document: Python daemon, own schema,
FTS5 bigram tokenisation, sqlite-vec, own MCP/HTTP, same stack as Winnie.

| Dimension | A: qmd as-is | B: qmd SDK | C: self-built |
|---|---|---|---|
| First-version work | ~0 code: unit, timer, skill | 500–1000 lines TS | ~2–4k lines Python |
| Chinese lexical | **Broken**: its FTS5 table is `tokenize='porter unicode61'`, and unicode61 was measured on this build to reject `天气` against `今天天气` | Same — the table is qmd's | Correct: bigram tokenisation |
| Chinese semantic | Works, but needs `QMD_EMBED_MODEL` swapped to a CJK model and a full re-embed | Same | Model is our choice |
| Freshness | `qmd update` on a timer; `get` reads the index, not the file | Can add a stat-check wrapper | Read-time revalidation built in |
| MCP | Built in | Ours, on top of qmd | Ours |
| HTTP | Built in (`/query`, `/health`) | Ours | Ours |
| Winnie integration | Calls qmd's HTTP | Calls our HTTP | Same process family, same stack |
| Ops | One Node process, a timer, ~2 GB of GGUF models | Same, plus our daemon | One Python process |
| Evolvability (code roots, exclusions, scoring) | Constrained by upstream | Medium | High |
| Upstream risk | 2.8.3, active, MIT; 2.x API still moving | Same | None, but every fix is ours |
| Time to a usable answer | Hours | Days | 1–2 weeks |

Where the choice actually turns:

- **Chinese lexical search is the differentiator.** qmd's tokenizer makes
  two-character Chinese words unmatchable through FTS5, so a qmd-based system
  answers Chinese lexical queries only through dense retrieval and reranking.
  That may be good enough — but it is an empirical question, and the only honest
  way to settle it is to measure.
- **qmd's rerank and query expansion run on CPU here** (1.7B expansion model,
  0.6B reranker). Its `search` path is fast; its `query` path is not. A
  latency budget under 300 ms p95 probably rules out `query` on this host.
- **The skill is identical in all three options**, so it is not a differentiator;
  neither is the MCP surface shape, which qmd already matches closely.

**Decision (2026-11-01):** option **C**, self-built, chosen because Chinese
lexical retrieval is the primary use and only C gets it right; the bake-off was
dropped as unnecessary once that was the deciding constraint.

**Recommendation (historical).** Do a short bake-off before committing (an hour, mostly model
download): index a 500-file
sample with `qmd` (dense model swapped to Qwen3-Embedding) and with a ~100-line
SQLite FTS5-bigram prototype, run the same 20 Chinese queries, and compare
recall@5 and latency. Then:

- if qmd's dense path clears the fixture → take **A**, and only build **C** when
  a requirement it cannot meet appears (code roots, sensitive exclusions, scoring
  control);
- if Chinese recall collapses without the bigram index → take **C**, starting
  with P1 (lexical only), and treat qmd as the reference for the MCP surface;
- **B** only if a Node runtime is acceptable and you want qmd's ingestion without
  owning it — it does not fix the Chinese lexical gap.

## 14. Rejected alternatives

| Alternative | Why not |
|---|---|
| **RAGFlow** | Excellent chunking, but Docker + Elasticsearch + an LLM stack for one user; this host has 68 GB free |
| **FTS5 trigram** | Measured: two-character Chinese queries return nothing |
| **FTS5 ICU** | Not compiled into this SQLite (`no such tokenizer: icu`) |
| **Qdrant now** | 160k vectors do not justify a second process and a synchronisation problem; kept as the scale-out |
| **inotify-first** | 104k watches and a correctness story that still needs a sweep; the sweep is the mechanism, the watcher is latency |
| **Scripts or directory recipes in the skill** | Ties every client to a shell and teaches path knowledge that goes stale; MCP/HTTP are the stable contract |
| **A skill per root** | Multiplies trigger ambiguity in a catalog of 134 skills; one skill with a `roots` filter is enough |
| **Document text inside the skill** | The skill is loaded by every agent; it is instructions, not a data channel |
| **Re-index on every query** | Read-time revalidation gets the same guarantee for one `stat` |

## 15. Open questions

1. **Roots** — deferred by request; v1 indexes nothing until at least one root is
   listed in the YAML.
2. **Chinese source or mixed?** All measurement and tokenisation assumes
   Chinese is primary. If most of the corpus is English, the bigram index is
   still correct; it is just less of an advantage.
3. **qmd bake-off or commit directly to C?** The bake-off costs an hour and
   settles the one open technical question; skipping it commits to ~2–4k lines
   of Python on a hunch.
4. **MCP registration details** — the DSH overlay key, and whether Cursor's
   `.cursor/mcp.json` accepts a URL-only server entry, are confirmed in P3.
5. **Embedding model** — `BAAI/bge-m3` is the candidate; ONNX/GGUF availability
   and CPU throughput are measured before adoption.

## 16. Verified versus assumed

Verified on this host: file and byte counts for the v1 subset, exact duplicate
count, scan time, directory count, inotify limits, free disk, SQLite version and
tokenizer behaviour (trigram, ICU, bigram), CPU/RAM/GPU, Python and Node
versions, existence of `systemctl --user` services and `Linger=no`.

Verified from upstream: Cursor 3.x discovers Agent Skills in `~/.agents/skills/`
and the Claude/Codex directories; DSH's filesystem provider scans
`<agentsHome>/skills` and watches it; qmd 2.8.3 is MIT, requires Node ≥ 22, was
published 2026-08-16, ships an MCP server and HTTP search, and builds its FTS5
table with `tokenize='porter unicode61'` (read from the published tarball).

Not yet verified, checked at the start of the phase that needs it:

- `sqlite-vec` loads in this Python build and its vector type/distance syntax;
- `BAAI/bge-m3` ONNX or GGUF availability and throughput on this CPU;
- qmd's dense retrieval quality on Chinese with a swapped embedding model, and
  the latency of its `query` path on this host;
- whether DSH's MCP client accepts loopback HTTP or needs the stdio shim;
- that the skill's description triggers on the owner's real phrasing (test 6).

## 17. Implementation status (2026-11-01)

`packages/winnie-index` contains the v1 build: sweep, decode gate, chunker, FTS5
with bigram terms, BM25 search, revalidating `get`, MCP (stdio and loopback
HTTP), the HTTP API, the Skill installer and a `systemctl --user` unit template.

| Check | Result |
|---|---|
| Test suite | 77 passed (config, decode, tokenizer, chunker, scan/search, MCP, HTTP, admin, skill) |
| API suite | 118 passed, including the proxy tests |
| Smoke corpus | 62 files / 448 KB over three roots → 497 chunks in 0.18 s |
| Search | `部署`, `决定`, `知识库` and `MCP server` return the expected paths and headings |
| HTTP | `/healthz`, `/search`, `/doc`, `/status` and `/mcp` answered on 127.0.0.1 |
| MCP | `initialize`, `tools/list` and `tools/call` answered over HTTP; stdio reads the same dispatcher |
| Skill | Rendered from the live config, so the port and the rules cannot drift |
| `get` freshness | An out-of-band edit is re-read and re-chunked before the text is returned |
| Admin API | Save rewrites the YAML, reloads without a restart, and retires removed roots |
| Console | Firefox against a live API and daemon: the page renders roots and counts, a dry run reports its counts, saving reloads the daemon |

Commands, from the repository root:

```bash
make index-dry      # what would be ingested, writes nothing
make index-build    # sweep and index
make index-search Q="部署"
make index-status
make index-serve    # daemon: sweep loop, HTTP and MCP on 127.0.0.1:8787
make index-skill    # write ~/.agents/skills/local-knowledge/SKILL.md
make index-test
```

Deviations from the design above, each deliberate:

- **No `jobs` table.** Work is derived from the diff on every sweep, so a crash
  resumes without a queue. The table arrives with embedding, when work becomes
  long enough to schedule.
- **No vectors.** BM25 with bigram terms answers the "what did I write" case
  already; dense retrieval is P2 behind the same `VectorStore` seam.
- **No `min_score`.** Hits are filtered by query-term coverage instead, because a
  BM25 threshold has no absolute meaning. The score threshold arrives with the
  dense scores that make it meaningful.
- **Headings are indexed.** A section is findable by its heading even when the
  body does not repeat the word; the heading stays out of `chunk.text` so the
  citation remains exact.
- **The sweep is synchronous.** At 19k files a full build is seconds; a worker
  pool would be complexity without a measured need.

What remains: dense retrieval and RRF (P2), inotify acceleration, the reranker,
the 40-query evaluation fixture, and the DSH/Cursor registration check that the
stdio shim and HTTP transport both negotiate correctly with real clients.

Verification note: the console was driven in Firefox against a live API and a
live daemon on 2026-11-01 — the page rendered 61 documents and the three roots,
the dry-run button reported its counts, and a save rewrote the YAML and reloaded
the daemon. That run is also how the missing bounds were found: the sweep
interval accepted `300600` from a number field, so the server now enforces
5–86400 s and the form carries the same limit.
