# Local document index — technical design

**Status:** proposed for review. Nothing here is implemented.

**Scope, as of 2026-11-01:** text files only — Markdown, plain text and source
code. No PDF, Office, OCR, e-mail or image ingestion. The server is always on.
The same index is consumed through three surfaces: an **Agent Skill** (primary,
for DSH, Cursor and Claude Code), **MCP** and **HTTP** (for Winnie and anything
else).

**Goal.** One local index over the owner's text files, kept in step with the
filesystem, that any coding agent can query without being told the paths, and
that Winnie's assistant can use as one more tool — with every answer traceable
to a file and a line range.

**Non-goals.** Cloud sync, multi-user access control, a web UI, a write path
into the indexed files, and replacing the assistant's long-term memory: memory
holds distilled facts about the owner, this index holds the source text those
facts can be checked against.

## 1. What the index must do

| Requirement | Consequence |
|---|---|
| Text only, ~45k files, several roots, outside the repo | Roots are configuration; the index lives outside the checkout |
| Code is in scope | Source files are chunks with a `path :: symbol` header, not prose |
| Files change often | Incremental, content-addressed ingestion; never re-read everything per change |
| "Always the latest version" | A bounded-staleness contract plus read-time revalidation (§3) |
| Agents must find it unprompted | A Skill whose description carries the trigger phrases, not just a server (§8.1) |
| Winnie must use it too | HTTP API and one assistant tool, separate from memory |
| One user, one machine, always on | One daemon under systemd; no Elasticsearch, no cluster |

"Always latest" is not free: a derived index only knows what a scan or a
filesystem event told it. This design states the window per guarantee and closes
the rest at read time instead of pretending it does not exist.

## 2. Measured corpus (this host, 2026-11-01)

| Measurement | Value |
|---|---|
| Candidate files after the scope rules (text extensions, ≤ 2 MB, caches excluded) | **44,943** |
| Raw bytes | **403.5 MB** |
| Unique content (SHA-1 over every candidate, counted once) | **37,773 files / 365.0 MB** |
| Exact duplicates | **7,170 files / 38.5 MB** (16 %) |
| Prose (`md txt rst org tex`) | 19,359 files / 180.5 MB |
| Code (`py go js ts tsx jsx java kt rs c cpp h sh sql`) | 13,135 files / 124.3 MB |
| Config (`yaml json toml ini conf cfg`) | 12,449 files / 98.7 MB |
| Hashing throughput | 403 MB in 7.3 s, including one process per 100 files |
| Metadata scan of 202,798 files | 1.0 s warm (one `EACCES` on a root-owned `postgres` data dir) |
| Directories under `~/Projects` alone | 104,762 (inotify limit 1,048,576) |
| Free disk | 68 GB (93 % used) |
| Cores / RAM / GPU | 16 / 26 GB / none |
| SQLite | 3.46.1, FTS5 available, ICU tokenizer absent |
| Python | 3.14.4 system, 3.13 present, `uv` absent |

Estimated from those bytes: **200–250k chunks**, index **1.5–2.5 GB**,
one-off embedding **1–2 hours** on CPU. The phase-0 dry run replaces every
estimate with a count.

Two findings still shape the rules, even after the scope narrowed:

1. **Extension alone is not enough.** Under the text allowlist, 1,345 files
   ending in `.ts` are 6.9 GB of MPEG transport-stream segments in
   `~/Projects/Mine/cinema/data/cache` — not TypeScript. A size cap and cache
   exclusions remove them, and a decode gate (§7.1) catches whatever a future
   directory invents.
2. **Duplicates are material.** 16 % of files are byte-identical to another
   file; by size alone the figure looked like 57 %. Exact hashing is worth
   doing before embedding, because every duplicate is an embedding paid twice
   and a result slot wasted.

## 3. The freshness contract

| Level | Guarantee | Mechanism |
|---|---|---|
| Read | A document handed to a model is never stale | `get` stats the file; on any difference it re-reads and re-chunks before returning text |
| Search | Index reflects changes within one sweep (default 300 s); within ~2 s where the watcher is active | sweep diff plus debounced inotify events |
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
`~/.local/share/winnie-index/` holds `catalog.sqlite` (WAL), `index.log` (JSON
lines) and `backups/`.

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
        ┌─────────────────┬───────────────────┼───────────────────┐
        ▼                 ▼                   ▼                   ▼
  Skill (SKILL.md)   MCP server        HTTP API /search      Winnie tool
  via scripts/       stdio + HTTP      /doc /status          (assistant)
```

One daemon owns the database, the scanner, the queue and both indexes. One
process means no cross-process locking and no second service: a query can never
see chunks the catalog has already deleted.

### 4.1 Configuration

One YAML file; the values below are the built-in defaults, not required keys.
Precedence is `defaults < YAML < WINNIE_INDEX_* environment < flags`, unknown
keys are rejected at startup, and a missing file at the default path is fine
while a missing file passed with `--config` is an error.

```yaml
# ~/.config/winnie-index/config.yaml
data_dir: ~/.local/share/winnie-index

roots:
  - name: documents
    path: /home/catroll/Documents
    tier: dense
    exclude: ["xwechat_files/**", "**/CopyQ/**"]
    watch: false
  - name: notes
    path: /home/catroll/Notes          # gitpim calendar: journals and events
    tier: dense
  - name: projects
    path: /home/catroll/Projects
    tier: dense
    exclude: ["**/node_modules/**", "**/.git/**", "**/.venv/**", "**/dist/**",
              "**/build/**", "**/target/**", "**/.gradle/**", "**/data/cache/**"]

formats:
  prose:  [md, markdown, txt, rst, org, tex]
  code:   [py, go, js, ts, tsx, jsx, java, kt, rs, c, cpp, h, sh, sql]
  config: [yaml, yml, json, toml, ini, conf, cfg]

scan:
  interval_seconds: 300
  watch: false
  debounce_ms: 1500

index:
  max_file_mb: 2
  max_chunks_per_doc: 2000
  chunk_tokens: 500
  overlap_tokens: 60
  vectors: sqlite-vec            # sqlite-vec | qdrant
  qdrant_url: http://127.0.0.1:6333
  dense:
    enabled: true
    include: [prose, code]       # config stays lexical: it is keyword-shaped
    model: BAAI/bge-m3
    dims: 1024
    batch: 16

retrieval:
  bm25_k: 50
  dense_k: 50
  rrf_k: 60
  max_per_doc: 2
  limit: 8
  min_score: 0.25                # below this, return nothing

serve:
  http: 127.0.0.1:8787
  mcp: http                      # http | stdio
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
  sensitive INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
  watch INTEGER NOT NULL DEFAULT 0
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
  bytes_hash TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  encoding TEXT NOT NULL,
  meta TEXT NOT NULL           -- JSON: decoded lines, warnings, binary sniff
);

CREATE TABLE chunks (
  id INTEGER PRIMARY KEY,
  doc_id INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  heading TEXT,                -- Markdown heading path, or `path :: symbol` for code
  start_line INTEGER, end_line INTEGER,
  text TEXT NOT NULL,
  terms TEXT NOT NULL,         -- pre-tokenised: CJK bigrams + ASCII words
  tokens INTEGER NOT NULL,
  UNIQUE(doc_id, seq)
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  terms, content='chunks', content_rowid='id', tokenize='unicode61');

CREATE VIRTUAL TABLE chunk_vectors USING vec0(
  chunk_id INTEGER PRIMARY KEY, embedding float[1024] distance_metric=cosine);

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY, doc_id INTEGER,
  kind TEXT NOT NULL,          -- decode|chunk|embed|delete
  state TEXT NOT NULL,         -- pending|running|done|failed
  priority INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, not_before TEXT, created_at TEXT NOT NULL
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version, tokenizer, embedding_model, embedding_dims, last_sweep_at
```

`embedding_model` and `embedding_dims` are load-bearing: changing the model
without a full re-embed leaves two vector spaces in one table, which presents as
a mysterious quality regression. Startup refuses to serve dense results when
`meta` disagrees with the config; `embed --force` is the only way through.

## 6. Ingestion

### 6.1 Scope rules

Evaluated in order, all configurable per root:

1. root `include` / `exclude` globs;
2. extension allowlist for `prose`, `code`, `config` (§4.1);
3. per-file cap 2 MB (`sealed` above it, counted, not failed);
4. excluded by default: `.git`, `node_modules`, `.venv`, `dist`, `build`,
   `target`, `data/cache`, hidden dirs, `*.min.*`, lock files;
5. symlinks not followed; each root walked with `xdev` so a mount cannot pull in
   200 GB silently.

The cap and the cache exclusion are what keep 6.9 GB of `.ts` video segments out
of a corpus that is otherwise 403 MB.

### 6.2 Decoding gate

No parser zoo, because there is nothing to parse — but "it ends in `.md`" is not
proof that it is text:

1. read at most 2 MB;
2. refuse a NUL byte in the first 8 KB (`sealed: binary`);
3. decode as UTF-8, then UTF-8-SIG, then GB18030; failure seals the file with
   the attempted encodings in `last_error`;
4. normalise CRLF to LF and strip the BOM, but **never** strip leading
   whitespace — indentation is content in code;
5. store the text under `bytes_hash` and record the encoding used.

GB18030 is tried last rather than `latin-1`, because a mis-decoded Chinese file
becomes mojibake that embeds, ranks and is cited. A file that cannot be decoded
is visibly sealed, not silently garbled.

### 6.3 Chunking

| Kind | Boundary | Header |
|---|---|---|
| prose | Markdown headings, then paragraphs; 500 tokens, 60 overlap | ancestor heading path, e.g. `Deploy > Rollback` |
| code | top-level symbol boundaries when tree-sitter knows the language, otherwise blank-line blocks; 60-line windows, 10-line overlap | `path :: symbol` |
| config | line blocks; 60-line windows | `path` |

- Keep `start_line`/`end_line` so a citation is actionable and the model can
  read exactly the cited region.
- Never split a fenced code block that fits; if one exceeds the cap, keep it
  whole and mark it truncated.
- Per-document cap of 2,000 chunks so one generated file cannot dominate the
  index.

### 6.4 Dedupe

- **Exact:** `contents` keyed by `bytes_hash`; identical bytes share one decode
  and one set of chunks, while `docs` keeps every path.
- **Near:** `text_hash` after line-ending normalisation. Search collapses hits
  sharing a `text_hash` into one result whose `also_at` lists the other paths.
  Without this, 7k duplicate files become 7k duplicate hits.

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
| Store | `sqlite-vec` in the catalog file | ~200k × 1024 × 4 B ≈ 800 MB f32, ~200 MB int8; brute-force KNN is ~50–150 ms and transactional with the rows it points at. A second service is not justified |
| Scale-out | Qdrant behind a `VectorStore` port | Trigger: p95 > 500 ms, > 500k vectors, or a second reader host. The project's earlier Qdrant choice survives as the escape hatch, not the first step |
| Model | multilingual, 1024-d, CPU-feasible — `BAAI/bge-m3` as candidate | Chinese-heavy corpus; no GPU on this host; verified for ONNX/GGUF availability before adoption |
| Tiering | prose and code dense; config lexical only | Config is keyword-shaped; embedding 12k YAML/JSON files pays for little recall |
| Build | one-off batch, resumable per document | ~200k chunks at 30–60 chunks/s is 1–2 hours of CPU, then incremental |

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
  An index that always returns eight chunks teaches the model to answer from
  the wrong file.
- Every hit carries `path`, `heading`, `start_line`–`end_line`, `mtime`,
  `content_hash`, `score`, a snippet, and `fresh` (false means the file changed
  after indexing and the text was re-read at query time).

## 8. Delivery surfaces

Three surfaces, one daemon, three audiences. They must not drift, so all three
are rendered from the same catalog and the same query code.

### 8.1 Agent Skill (primary)

[Agent Skills](https://cursor.com/docs/skills) is the open `SKILL.md` standard:
a directory with a short `SKILL.md`, optional `references/` loaded on demand,
and optional `scripts/`. Cursor 3.x loads it from `.agents/skills/`,
`.cursor/skills/`, `~/.agents/skills/`, `~/.cursor/skills/` and, for
compatibility, the Claude and Codex directories. DSH's filesystem provider scans
`<project>/.dsh/skills`, `<project>/.agents/skills`, configured custom
directories, `<dshHome>/skills` and `<agentsHome>/skills`, and watches them, so a
regenerated skill reaches the next session without a restart. One canonical
install therefore serves DSH, Cursor and Claude Code with no symlinks:

```
~/.agents/skills/local-knowledge/
  SKILL.md                 # ~100 lines; the only file loaded by default
  references/
    roots.md               # generated: what is indexed, what is not, counts
    query-syntax.md        # filters, citation format, worked examples
  scripts/
    search.sh              # thin wrapper over the HTTP API, --json output
    get.sh
    status.sh
```

`SKILL.md` content, in order:

| Part | Content |
|---|---|
| `name` | `local-knowledge` |
| `description` | Trigger phrases in Chinese and English: "我的笔记", "之前写的", "找一下那份文档", "where did I", "what did we decide about". Plus: prefer the `search_documents` tool, else `scripts/search.sh` |
| `whenToUse` | DSH's optional field, same trigger sentences; Cursor ignores it, so the description must stand alone |
| When to use | Questions about the owner's own writing, decisions, code; not for public facts (web search) and not for current events |
| Workflow | 1) `search` with a specific query, 2) read the cited lines with `get`, 3) answer with `path:line` and quote only what was read |
| Limits | Text only: no PDF, no Excel, no images; what to say when nothing matches |
| Freshness | Results carry mtime; `get` revalidates; re-run search after a file changes |

Regeneration: `winnie-index skill install` renders the templates with live root
names, counts and gaps; `--check` reports drift and exits non-zero. The templates
live in the repo, the installed copy lives in `~/.agents/skills`, and a schema or
root change regenerates `references/roots.md` — never per-file churn.

Two constraints on the skill, because every agent pays for its description in
context:

- `SKILL.md` stays under ~120 lines; detail belongs in `references/`.
- Neither `SKILL.md` nor `references/` ever contains document text or long path
  lists — only instructions, root names and counts. A skill is context, not a
  data channel.

Why one skill rather than one per root: the catalog already holds 134 skills,
and a per-root split multiplies the trigger ambiguity ("was this note in Notes or
Documents?"). One skill with a `roots` filter argument is enough.

### 8.2 MCP

| Tool | Arguments | Returns |
|---|---|---|
| `search_documents` | `query`, `roots?`, `kind?`, `since?`, `limit?` | ranked hits with citation fields; an empty list is a valid answer |
| `get_document` | `path`, `from_line?`, `lines?` | revalidated text with line numbers |
| `index_status` | — | counts by state, last sweep, lag, failures, disk, model |

Transport: loopback HTTP (`POST /mcp`, streamable, stateless) as the default,
with a `stdio` shim that proxies to the daemon. HTTP is preferred because the
daemon already runs and model loading/index state should be shared; stdio exists
for clients that cannot reach a port. Tool descriptions are written for
selection, not documentation: they say *when* to call, not what the fields mean.

### 8.3 HTTP

`POST /search`, `GET /doc/{id}`, `GET /status`, `GET /healthz`. Loopback only;
a non-loopback bind requires `WINNIE_INDEX_TOKEN` and warns at startup. This is
what Winnie's assistant calls, and what the skill's scripts fall back to when the
MCP server is not configured.

### 8.4 Winnie

One assistant tool, `search_documents`, modelled on the existing `memory_search`
contract: plain text, bounded, `path:line` citations, empty when nothing
matches. Memory and documents remain separate tools — memory is what the
assistant was told, the index is where the source is.

## 9. Operations

| Concern | Decision |
|---|---|
| Process | one daemon (`systemd --user` unit, `Restart=on-failure`), `loginctl enable-linger` so it survives logout; sweep on start, then every 300 s |
| Initial build | `winnie-index build --dry-run` prints counts and bytes per root/format and writes nothing; the real build is resumable and safe to interrupt |
| Order | lexical for all roots first (usable in minutes), dense in the background — a half-finished build is already useful |
| Backpressure | the scanner pauses when pending jobs exceed the threshold; `status` shows queue depth |
| Disk | budget 4 GB (index 1.5–2.5 GB, scratch, 2 snapshots); warn below 30 GB free, refuse to embed below 15 GB (68 GB today) |
| Growth | daily `PRAGMA optimize`, weekly `VACUUM` if freed pages > 10 %, 2 daily `sqlite3 .backup` snapshots (the index is rebuildable from files; snapshots are convenience, not backup) |
| Observability | JSON lines: one entry per sweep, batch and failure; `/healthz` returns queue depth and last-sweep age |
| Doctor | `winnie-index doctor` checks sqlite-vec load, FTS5, model dims, watch limits, free space, skill drift |

```
winnie-index init | scan | build [--dry-run] | embed | search | get
             status | doctor | skill install --check | mcp [--http|--stdio] | gc
```

## 10. Security and privacy

- Local only; no network egress in the default configuration.
- Catalog mode `0600`, directory `0700`.
- HTTP/MCP bind `127.0.0.1`; non-loopback requires a token.
- Per-root `sensitive: true` excludes a root from default searches, demands an
  explicit `roots` argument, and redacts its paths in logs and status output.
- Logs record paths, sizes, hashes and errors — never document text.
- `get_document` refuses paths outside configured roots, so a crafted path cannot
  read `~/.ssh`.
- The skill is world-readable by design (agents load it); it must therefore never
  contain corpus text or sensitive paths.

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
   selected, the search tool is called, the answer cites `path:line`.

Test 6 is the one that decides whether the skill is doing its job, and it is the
one most easily skipped.

## 12. Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 Inventory** | `build --dry-run`, scope rules, per-root/format report | counts match §2 within 5 %; nothing written |
| **P1 Lexical + Skill** | catalog, sweep, decode gate, chunker, FTS5, `search`/`get` CLI, skill install | 44k files indexed; p95 < 200 ms; rename/delete/touch tests pass; an agent answers from the skill without being given a path |
| **P2 Code** | symbol chunking, `kind` filters, dedupe collapse | code queries return the enclosing symbol; duplicates collapse |
| **P3 Dense** | embeddings, sqlite-vec, RRF | hybrid recall@5 ≥ lexical + 0.1; build time and disk recorded |
| **P4 MCP + Winnie** | MCP HTTP/stdio, HTTP API, assistant tool | one Winnie answer cites an indexed file; MCP client lists the tools |
| **P5 Operations** | watcher, systemd, linger, gc, doctor | 7 days unattended; lag within target |

Each phase ends with a commit carrying its measured numbers, so later tuning has
a baseline to beat.

## 13. Rejected alternatives

| Alternative | Why not |
|---|---|
| **qmd** as the whole solution | The closest off-the-shelf fit — text/markdown/code over SQLite FTS5 + sqlite-vec, incremental `update`, MCP, and an HTTP search endpoint. Rejected because it generates no Skill, its lexical index uses FTS5's default tokenizer (no CJK bigrams), freshness is driven only by an explicit `qmd update` with no read-time revalidation, and its GGUF embeddings would run slower here than an ONNX path on a CPU-only host. Keep it as the reference implementation for the MCP surface |
| **RAGFlow** | Excellent chunking, but Docker + Elasticsearch + an LLM stack for one user; this host has 68 GB free |
| **FTS5 trigram** | Measured: two-character Chinese queries return nothing |
| **FTS5 ICU** | Not compiled into this SQLite (`no such tokenizer: icu`) |
| **Qdrant now** | 200k vectors do not justify a second process and a synchronisation problem; kept as the scale-out |
| **inotify-first** | 104k watches and a correctness story that still needs a sweep; the sweep is the mechanism, the watcher is latency |
| **A skill per root** | Multiplies trigger ambiguity in a catalog of 134 skills; one skill with a `roots` filter is enough |
| **Putting document text in the skill** | The skill is loaded by every agent into its context; it is instructions, not a data channel |
| **Re-index on every query** | Recorded because "always latest" invites it; the read-time revalidation gets the same guarantee for one stat |

## 14. Open questions

1. **Exact roots.** The measurement covered `~/Documents`, `~/Projects`,
   `~/Notes`, `~/Desktop`; 42,435 of the 44,943 files are under `~/Projects`.
   Should `~/Downloads` be included at all?
2. **Code roots.** Include all of `~/Projects`, or only repositories the owner
   edits? A vendored library tree adds noise to every code query.
3. **Config files.** 12,449 YAML/JSON files, 98.7 MB — lexical only, or drop them
   from the index entirely?
4. **Skill name and triggers.** Is `local-knowledge` the name agents should see,
   and do the Chinese trigger phrases match how you actually ask?
5. **MCP transport.** HTTP on `127.0.0.1:8787` as the default, stdio shim for
   clients that need it — or stdio only?
6. **Sensitive roots.** Anything under these paths that must never appear in an
   agent's context (tokens, credentials, work material)?
7. **Scope of "always on".** Does the daemon start at boot (system service)
   or at login (user service with linger)?

## 15. Verified versus assumed

Verified on this host: file and byte counts, exact duplicate count, hashing
throughput, scan time, directory count, inotify limits, free disk, SQLite version
and tokenizer behaviour (trigram, ICU, bigram), CPU/RAM/GPU, Python versions,
absence of `uv`, and that Ollama is installed but not runnable by this user.
Verified from upstream: Cursor 3.x discovers Agent Skills in `~/.agents/skills/`
and the Claude/Codex directories, and DSH's filesystem provider scans
`<agentsHome>/skills` among its roots and watches them for changes.

Not yet verified, checked at the start of the phase that needs it:

- `sqlite-vec` loads in this Python build and its vector type/distance syntax;
- `BAAI/bge-m3` ONNX or GGUF availability and throughput on this CPU;
- tree-sitter coverage for the code languages that matter here;
- whether DSH's MCP client accepts the loopback HTTP transport or needs the
  stdio shim;
- that the skill's description actually triggers on the owner's real phrasing
  (test 6 in §11).
