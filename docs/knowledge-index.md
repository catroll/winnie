# Local document index — design and plan

**Status:** proposed. Nothing here is implemented. The corpus (which roots, which
formats, whether OCR is wanted) is confirmed only for the measurements in
section 2, not for the scope rules in section 6.

**Goal.** One local index over roughly 100,000 files spread across several
directories outside this repository, continuously kept in step with the
filesystem, served to LLM clients so an answer can cite the file and line it
came from.

**Non-goals.** No cloud sync, no multi-user access control, no web UI, no
write path into the indexed files, and no replacement for the assistant's
long-term memory: memory holds distilled facts about the owner, this index holds
the source documents those facts may be checked against.

## 1. What the index must do

| Requirement | Consequence |
|---|---|
| ~100k files, several roots, outside the repo | Roots are configuration, not code; the index lives outside the checkout |
| Files change often | Incremental, content-addressed ingestion; never a full re-read per change |
| "Always the latest version" | A bounded-staleness contract, plus read-time revalidation (section 3) |
| The answer must be traceable | Every hit carries path, heading, line range, mtime and content hash |
| Mixed languages, mostly Chinese | CJK-aware lexical matching; a multilingual embedding model |
| One user, one machine | No Elasticsearch, no cluster, no second box |

"Always latest" cannot be free: a derived index can only know what a scan or a
filesystem event told it. This design makes the window explicit and closes the
remaining gap at read time instead of pretending it does not exist.

## 2. Measured on this machine (2026-11-01)

These numbers size the work. Every figure below came from this host, not from a
guess.

| Measurement | Value | How |
|---|---|---|
| Total candidate files (`~/Documents`, `~/Projects`, `~/Notes`, `~/Desktop`; dependency and build dirs pruned) | 202,798 | `find … -type f \| wc -l` |
| Of those, files with a text-bearing extension | 50,810 | extension allowlist, see 6.1 |
| Raw bytes of those files | 15.4 GB | `stat -c %s` per path |
| Files at most 2 MB, cache dirs excluded | 49,784 files / 1.02 GB | |
| Size-identical duplicate candidates (>1 KB) | 5,678 groups, 24,693 files, 19,015 redundant | `sort \| uniq -c` on size only |
| Metadata-only scan of 202,798 files | 1.0 s warm, one `EACCES` on a root-owned `postgres` data dir | `time find … > /dev/null` |
| Directories under `~/Projects` alone | 104,762 | `find -type d` |
| Free disk | 68 GB (93 % used) | `df -h` |
| Cores / RAM / GPU | 16 / 26 GB / none | `nproc`, `free`, `nvidia-smi` |
| SQLite | 3.46.1, FTS5 yes, ICU tokenizer no | `pragma compile_options` |
| Python | 3.14.4 system, 3.13 also present; `uv` absent | |

Three findings changed the plan:

1. **Extension is not enough.** 6.9 GB of the 15.4 GB is 1,345 files ending in
   `.ts` under `~/Projects/Mine/cinema/data/cache` — MPEG transport-stream
   segments, not TypeScript. Another 3.5 GB is one generated `ipv4_base.txt`,
   and 2.9 GB is JSON dumps under `Documents/Mine/SendCloud`. Without a
   per-file size cap and cache exclusions, a "text" corpus is 90 % waste.
2. **Duplication is large.** Even by size alone, 19,015 of 50,810 files are
   redundant. Content hashing will collapse more. Dedupe is not an optimisation
   here; it is required to keep results usable.
3. **Scanning is cheap, watching is not free.** A full metadata walk of 200k
   files takes about a second warm, so a periodic sweep is the robust primary
   mechanism. Watching every directory would mean ~110k inotify watches
   (limit 1,048,576; roughly 100 MB of kernel memory), which is affordable but
   not free, and gains seconds.

Estimated working set after scope rules: **~50k documents, 300–500k chunks**,
of which only "tier dense" documents (section 7.2) get embeddings — measured
tier-1 candidates: ~22k files (`md` 15,096, `txt` 4,144, `pdf` 579, `docx` 452,
`doc` 289, `pptx` 178, `html` 913, `eml` 274, before dedupe).

## 3. The freshness contract

Staleness is stated per guarantee instead of being hand-waved.

| Level | Guarantee | Mechanism |
|---|---|---|
| Read | A document returned to a model is never stale | `get` stats the file; on any difference it re-extracts before returning text |
| Search | Index reflects changes within one sweep interval (default 300 s); within ~2 s where the watcher is active | sweep diff + debounced inotify events |
| Consistency | No ghost entries, no duplicated documents | tombstones on delete, content-hash dedupe, daily count reconciliation |

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

Consequences worth stating:

- An editor that rewrites a file or a sync client that touches mtime costs one
  hash, not an extraction.
- Rename/move is detected by `(dev, ino)` first, then `(size, mtime_ns, hash
  prefix)`, and updates the path without touching chunks or vectors.
- Delete writes a tombstone, purges chunks and vectors asynchronously, and keeps
  the row for 30 days (`deleted_at`) so an accidental delete is recoverable.
- A daily reconciliation re-hashes a 1 % sample and every file whose mtime is in
  the future or predates its ctime, and compares row/chunk/vector counts.

The watcher is an accelerator, never the correctness mechanism. If it overflows
(`IN_Q_OVERFLOW`), is killed, or the root is a network mount, the sweep still
converges — the next sweep is authoritative. This is also why the watcher is
opt-in per root: `~/Projects` has 104k directories, `~/Notes` has 34.

## 4. Architecture

```
roots (YAML) ──► scanner ──► diff vs catalog ──► jobs (SQLite)
                    ▲                                 │
        inotify (optional, per root)                  ▼
                                            extractor pool (processes)
                                                      │ text
                                                      ▼
                                                   chunker
                                                      │
                              ┌───────────────────────┴────────────┐
                              ▼                                    ▼
                    FTS5 (bigram terms)                  sqlite-vec (dense)
                              └───────────────┬────────────────────┘
                                              ▼
                                    query: BM25 + KNN → RRF
                                              │
                              ┌───────────────┴───────────────┐
                              ▼                               ▼
                    MCP server (stdio / HTTP)        HTTP API + assistant tool
```

One daemon owns the database, the scanner, the job queue and both indexes. One
process means no cross-process locking, no second service, and a single
consistent view: a query can never see chunks that the catalog has already
deleted.

```
~/.local/share/winnie-index/
  catalog.sqlite        # docs, chunks, FTS5, vectors, jobs (WAL)
  index.log             # JSON lines
  backups/              # sqlite .backup snapshots, 7 daily
```

### 4.1 Configuration

One YAML file; the values below are the built-in defaults, not required keys.
Precedence is `defaults < YAML < WINNIE_INDEX_* environment < command-line
flags`, unknown keys are rejected at startup, and a missing file at the default
path is fine while a missing file passed with `--config` is an error.

```yaml
# ~/.config/winnie-index/config.yaml
data_dir: ~/.local/share/winnie-index

roots:
  - name: documents
    path: /home/catroll/Documents
    tier: dense
    exclude: ["xwechat_files/**", "**/CopyQ/**", "**/*.dat"]
    ocr: false
    watch: false
  - name: notes
    path: /home/catroll/Notes          # gitpim calendar: journals and events
    tier: dense
  - name: projects
    path: /home/catroll/Projects
    tier: lexical
    exclude: ["**/node_modules/**", "**/.git/**", "**/.venv/**", "**/dist/**",
              "**/build/**", "**/target/**", "**/.gradle/**", "**/data/cache/**"]

scan:
  interval_seconds: 300       # one sweep per five minutes
  watch: false                # inotify acceleration, per root override
  debounce_ms: 1500

extract:
  max_file_mb: 8
  max_text_chars: 2000000
  max_chunks_per_doc: 2000
  workers: 14                 # cores - 2

index:
  chunk_tokens: 500
  overlap_tokens: 60
  vectors: sqlite-vec         # sqlite-vec | qdrant
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
  min_score: 0.25             # below this, return nothing

serve:
  http: 127.0.0.1:8787
  mcp: stdio                  # stdio | http
```

## 5. Data model

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE roots (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL UNIQUE,
  path      TEXT NOT NULL,
  tier      TEXT NOT NULL DEFAULT 'lexical',   -- lexical | dense
  include   TEXT NOT NULL,                     -- JSON array of globs
  exclude   TEXT NOT NULL,
  ocr       INTEGER NOT NULL DEFAULT 0,
  watch     INTEGER NOT NULL DEFAULT 1,
  sensitive INTEGER NOT NULL DEFAULT 0,
  enabled   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE docs (
  id         INTEGER PRIMARY KEY,
  root_id    INTEGER NOT NULL REFERENCES roots(id),
  rel_path   TEXT NOT NULL,
  ext        TEXT NOT NULL,
  size       INTEGER NOT NULL,
  mtime_ns   INTEGER NOT NULL,
  ino        INTEGER NOT NULL,
  dev        INTEGER NOT NULL,
  bytes_hash TEXT,                 -- blake2b of raw bytes
  text_hash  TEXT,                 -- blake2b of normalised text
  tier       TEXT NOT NULL,
  state      TEXT NOT NULL,        -- new | indexed | failed | deleted | sealed
  attempts   INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  indexed_at TEXT,
  deleted_at TEXT,
  UNIQUE(root_id, rel_path)
);

-- Extraction is keyed by content, so a rename, a copy and a second path to the
-- same bytes all reuse one extraction.
CREATE TABLE contents (
  bytes_hash TEXT PRIMARY KEY,
  text       TEXT NOT NULL,
  meta       TEXT NOT NULL         -- JSON: extractor, pages, sheets, warnings
);

CREATE TABLE chunks (
  id         INTEGER PRIMARY KEY,
  doc_id     INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  heading    TEXT,
  page       INTEGER,
  start_line INTEGER,
  end_line   INTEGER,
  text       TEXT NOT NULL,
  terms      TEXT NOT NULL,        -- pre-tokenised: CJK bigrams + ASCII words
  tokens     INTEGER NOT NULL,
  UNIQUE(doc_id, seq)
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  terms, content='chunks', content_rowid='id', tokenize='unicode61');

CREATE VIRTUAL TABLE chunk_vectors USING vec0(
  chunk_id INTEGER PRIMARY KEY,
  embedding float[1024] distance_metric=cosine);

CREATE TABLE jobs (
  id         INTEGER PRIMARY KEY,
  doc_id     INTEGER,
  kind       TEXT NOT NULL,        -- extract | chunk | embed | delete
  state      TEXT NOT NULL,        -- pending | running | done | failed
  priority   INTEGER NOT NULL DEFAULT 0,
  attempts   INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  not_before TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version, tokenizer, embedding_model, embedding_dims, last_sweep_at
```

`meta.embedding_model` and `meta.embedding_dims` are load-bearing: a model change
without a full re-embed produces vectors from two spaces in one table, which
looks like a quality regression and is impossible to debug from the outside.
Startup refuses to serve dense results when they disagree with the config, and
`embed --force` is the only way out.

## 6. Ingestion

### 6.1 Scope rules

Order of evaluation, all configurable per root:

1. root `include` / `exclude` globs;
2. extension allowlist (`md, markdown, txt, rst, org, tex, pdf, docx, doc,
   pptx, xlsx, xls, csv, eml, html, htm, json, yaml, yml, toml, ini, conf, py,
   go, js, ts, tsx, jsx, java, kt, rs, sh, sql`);
3. per-file size cap (default 8 MB, `sealed` above it, counted not failed);
4. hidden dirs, `.git`, `node_modules`, `.venv`, `dist`, `build`, `target`,
   `data/cache`, `*.min.*` — excluded by default;
5. symlinks are not followed (loop protection); each resolved root is walked
   with `xdev` so a mount point cannot silently pull in 200 GB.

`ts` survives the allowlist deliberately: 1,345 files were 6.9 GB of video
segments, but the size cap and the `data/cache` exclusion remove them; a blanket
`.ts` exclusion would also drop real TypeScript.

### 6.2 Extraction matrix

| Format | Extractor | Notes |
|---|---|---|
| md, txt, code, yaml, json, csv | UTF-8, then UTF-8-SIG, then GB18030 | A GBK file read as UTF-8 becomes mojibake that embeds and ranks; encoding is tried, never assumed |
| pdf | `pypdf` text; Docling when layout/tables matter | Scanned pages yield nothing; recorded as `no_text`, not as failure |
| docx | `python-docx` (paragraphs + tables) | |
| doc | `antiword` or LibreOffice headless | Old binary format; missing tool ⇒ `sealed` with a reason |
| pptx | `python-pptx`, one chunk group per slide | Slide number becomes `page` |
| xlsx | `openpyxl` read-only, per sheet, header repeated | Row cap per sheet; the file is a table, not prose |
| eml | stdlib `email` | Subject, from/to, body; attachments not recursed |
| html | `trafilatura` or `selectolax` | Boilerplate removal matters more than the parser |
| images | none by default | OCR is opt-in per root (Chinese needs `chi_sim`) |

Extraction runs in a process pool (`max_workers` = cores − 2): several parsers
are CPU-bound C extensions and would serialise under threads. The result is
cached by `bytes_hash`, so a copy or rename never pays twice. Every failure
stores `last_error` from the parser and retries with backoff, bounded at 3
attempts; a permanently broken file is `failed`, visible in `status`, and never
stops the sweep. The same applies to a path that cannot be read: the scan today
already hits one (`Projects/Mine/WLB/tools/woody/docker/data/postgresql/data`,
`EACCES`), and a sweep that aborted on it would never finish on this host.

### 6.3 Chunking

- Break on structure: Markdown headings, PDF pages, slides, sheet boundaries,
  blank lines. Target 500 tokens, 60-token overlap, hard cap 1,200.
- Keep `heading` (the ancestor heading path, e.g. `Deploy > Rollback`) and
  `start_line`/`end_line` (or `page`) so a citation is actionable: the model can
  read exactly the cited region.
- Never split a fenced code block or a table that fits; if a code block exceeds
  the cap, keep it whole and mark it truncated.
- Per-document cap of 2,000 chunks (`sealed` beyond it) so one generated file
  cannot dominate the index.

### 6.4 Dedupe

Two levels, both cheap:

- **Exact:** `contents` keyed by `bytes_hash`; identical bytes share one
  extraction and one set of chunks. `docs` keeps every path.
- **Near:** `text_hash` after whitespace and markup normalisation. Search
  collapses hits that share a `text_hash` into one result whose `also_at` lists
  the other paths. Without this, 19k redundant files become 19k redundant hits.

## 7. Indexing and retrieval

### 7.1 Lexical

**CJK bigrams, pre-tokenised, indexed by FTS5 `unicode61`.** This mirrors
`winnie_assistant.memory.tokenize`, which already takes the same decision for
memory, so there is one tokenisation rule in the project rather than two.

Measured, twice:

- `tokenize='trigram'` matches `今天天气` but **not** `天气`: trigram needs three
  characters, and two-character Chinese words are the common case. Rejected
  after testing, not from reading.
- `tokenize='icu'` is unavailable in this SQLite build
  (`no such tokenizer: icu`), so dictionary segmentation through FTS5 is not an
  option without a custom C extension.
- Pre-tokenised bigrams: `天气`, `公园`, `散步` and phrase `今天天气` all match;
  ASCII words are unaffected; no new dependency.

Queries are tokenised by the same function. A query segment with more than one
term is sent as an FTS5 phrase (`"今天 天天 天气"`), distinct segments are joined
with AND, so multi-word queries do not degrade into a bag of bigrams.

Cost: the `terms` column roughly doubles token volume. Acceptable at this size,
and measured in phase 1.

### 7.2 Dense

| Decision | Choice | Reason |
|---|---|---|
| Vector store | `sqlite-vec` in the catalog file | 200k × 1024 × 4 B ≈ 800 MB f32, ~200 MB int8; brute-force KNN is ~50–150 ms and transactional with the chunks it points at. A second service is not justified yet |
| Scale-out | Qdrant behind a `VectorStore` port | Trigger: p95 search > 500 ms, > 500k vectors, or a second reader host. The project's earlier Qdrant choice is preserved as the escape hatch, not as the first step |
| Model | multilingual, 1024-d, ONNX or GGUF, CPU-feasible — `BAAI/bge-m3` as the candidate | Chinese-heavy corpus; no GPU on this host |
| Tiering | embeddings only for `tier: dense` roots/formats (md, txt, pdf, docx, doc, pptx, eml, html) | Sheets, JSON and code are keyword-shaped; embedding them triples build time for marginal recall |
| Build | one-off batch, resumable, checkpointed per document | ~22k documents / ~150k chunks at 20–50 chunks/s is 1–3 hours of CPU, then incremental |

Privacy default: local model, no vendor API. An API embedding provider may be
added behind an explicit flag that prints a warning, because sending a personal
corpus to a vendor is a different product, not a configuration detail.

### 7.3 Fusion and answer quality

```
BM25 top 50  ─┐
              ├─► RRF (k=60) ─► per-doc cap (2 chunks) ─► top 8 ─► optional rerank
dense top 50 ─┘
```

- RRF, not score normalisation: BM25 and cosine are not comparable, and a
  [50k-document comparison](https://builder.aws.com/content/3KKg66VgAc6DKoYg8ubOBiIoI49)
  puts hybrid above either backend alone.
- Per-document cap prevents one long file from filling every slot.
- Reranking is phase 4, and only after the golden set says hybrid is not enough.
- A relevance floor is mandatory: when nothing clears it, return nothing and say
  so. An index that always returns eight chunks teaches the model to answer from
  the wrong file.
- Results carry: `path`, `heading`, `start_line`–`end_line` (or page), `mtime`,
  `content_hash`, `score`, and a snippet. `fresh: false` means the file changed
  after indexing and the text was re-read at query time.

## 8. Serving the model

**MCP tools** (stdio and loopback HTTP from the same daemon):

| Tool | Arguments | Returns |
|---|---|---|
| `search_documents` | `query`, `roots?`, `ext?`, `since?`, `limit?` | ranked hits with citation fields; empty result is a valid answer |
| `get_document` | `path`, `from_line?`, `lines?` | text, revalidated against the file, with line numbers |
| `index_status` | — | counts by state, last sweep, lag, failures, disk, model |

**HTTP** for the assistant and any other client: `POST /search`, `GET /doc/{id}`,
`GET /healthz`, `GET /status`.

**Assistant integration:** one more tool, `search_documents`, modelled on the
existing `memory_search` contract — plain text, bounded, `path:line` citations.
Memory and documents stay separate tools: memory is what the assistant was told,
the index is where the source is.

## 9. Operations

| Concern | Decision |
|---|---|
| Process | one daemon: scanner thread, job workers, HTTP/MCP listeners; `systemd --user` unit with `Restart=on-failure` |
| Initial build | `winnie-index build --dry-run` prints counts and bytes per root and format and writes nothing; the real build is resumable and safe to interrupt |
| Scheduling | sweep inside the daemon; a `systemd --user` timer is the fallback when a long-running daemon is unwanted |
| Backpressure | when pending jobs exceed a threshold, the scanner pauses; `status` shows the queue depth |
| Disk budget | index ≤ 5 GB; startup warns below 30 GB free and refuses to embed below 15 GB (68 GB free today) |
| Growth | daily `PRAGMA optimize`, weekly `VACUUM` if pages freed > 10 %, 7 daily `sqlite3 .backup` snapshots |
| Observability | JSON lines: one entry per sweep, per batch, per failure; `/healthz` returns queue depth and last sweep age |
| Unattended | `winnie-index doctor` checks sqlite-vec load, FTS5, model dims, watch limits, free space |

The first build is the only expensive run. Order: lexical for all roots first
(usable within minutes), then dense for tier-1 in the background. That way a
half-finished build is already useful, which matters for a tool whose value is
"just ask it".

```
winnie-index init | scan | build [--dry-run] | embed | search | get
             status | doctor | mcp [--http] | gc
```

## 10. Security and privacy

- Everything local; no network egress in the default configuration.
- Catalog file mode `0600`, directory `0700`; the corpus contains personal and
  work documents.
- HTTP and MCP bind `127.0.0.1` only; a non-loopback bind requires a token and
  prints a warning at startup.
- Per-root `sensitive: true` excludes a root from default searches and demands
  an explicit `roots` argument; its paths are redacted in logs and in
  `index_status` output.
- Logs record paths, sizes, hashes and errors — never document text.
- `get_document` refuses paths outside the configured roots, so a crafted path
  cannot read `~/.ssh`.

## 11. Evaluation

A fixture of 40 queries with expected files, written before tuning:

| Metric | Target |
|---|---|
| recall@5 (lexical only) | ≥ 0.75 |
| recall@5 (hybrid) | ≥ 0.85 |
| MRR@10 (hybrid) | ≥ 0.6 |
| Empty-result accuracy (10 queries with no answer) | ≥ 0.8 |
| p95 search latency | < 300 ms |
| Index lag, watcher on | < 5 s |
| Index lag, watcher off | ≤ sweep interval |
| Freshness: edit a file, then `get` | always current |

Behavioural tests that are cheaper than they look, and catch the bugs that
matter:

1. copy a file into a second root → one result, two paths;
2. rename a file → same chunks, new path, zero embeddings recomputed;
3. touch a file without changing content → no extraction, no embedding;
4. delete a file → gone from search within one sweep, tombstone present;
5. append a paragraph → search finds the new text within one sweep.

## 12. Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 Inventory** | `winnie-index build --dry-run`, scope rules, per-root/format report | counts and bytes match section 2 within 5 %; nothing written |
| **P1 Lexical** | catalog, sweep, extraction for md/txt/code, chunker, FTS5, `search`/`get` CLI, MCP | 50k docs indexed; p95 < 200 ms; rename/delete/touch tests pass |
| **P2 Extraction** | pdf/docx/doc/pptx/xlsx/eml/html, encoding fallback, failure reporting | ≥ 95 % of tier-1 files indexed or sealed with a reason; failures visible in `status` |
| **P3 Dense** | embeddings, sqlite-vec, RRF, tiering | hybrid recall@5 ≥ lexical + 0.1 on the fixture; build time and disk recorded |
| **P4 Quality** | rerank, filters, per-doc cap, assistant tool, MCP polish | fixture targets met; assistant cites a document in a real answer |
| **P5 Operations** | watcher, systemd, gc, backups, doctor | 7 days unattended with no manual step; lag within target |

Each phase ends with a commit that includes its measured numbers, so a later
tuning change has a baseline to beat.

## 13. Rejected alternatives

| Alternative | Why not |
|---|---|
| **qmd** as the whole solution | Excellent shape (SQLite FTS5 + sqlite-vec + MCP, incremental `update`), but markdown-centric: no PDF/DOCX/XLSX extraction, and it would need the extractor this plan already builds. Keep it as the reference for the MCP surface |
| **RAGFlow** | Good chunking, but Docker + Elasticsearch + an LLM stack for one user; disk and RAM on this host cannot spare it |
| **FTS5 trigram** | Measured: two-character Chinese queries return nothing |
| **FTS5 icu** | Not compiled into this SQLite (`no such tokenizer: icu`) |
| **Qdrant now** | 150–200k vectors do not justify a second process and a synchronisation problem; kept as the documented scale-out |
| **inotify-first** | 110k watches and a correctness story that still needs a sweep; the sweep is the mechanism, the watcher is latency |
| **Whole-corpus embeddings** | 15 GB includes 6.9 GB of video segments and generated data; tiering and dedupe cut the work by an order of magnitude |
| **Re-index on every query** | Not seriously considered; recorded because "always latest" invites it |

## 14. Open questions

1. **Which roots, exactly?** The measurement covered `~/Documents`,
   `~/Projects`, `~/Notes`, `~/Desktop`. `~/Downloads` (10,028 files, 266 GB) and
   `~/Nutstore Files` (42 GB of video) look like they should not be indexed.
2. **Is OCR wanted?** 28,837 PNGs and 6,559 JPEGs exist in `~/Documents`; scanned
   PDFs are unknown. OCR changes build time and disk materially.
3. **Should code be dense-indexed?** `~/Projects` has 14k Markdown and 8k source
   files; lexical only is cheap and probably enough for "which file said X".
4. **Where may the index live?** Default `~/.local/share/winnie-index`
   (outside this checkout, as the corpus is).
5. **Which clients consume it?** MCP for DSH/Claude/Cursor, HTTP for Winnie's
   assistant, or both.
6. **Is the machine always on?** If not, the daemon needs a catch-up sweep on
   boot and the lag target applies from boot, not from wall-clock.

## 15. Verified versus assumed

Verified on this host: the file counts, byte counts, duplicate estimate, scan
time, directory count, inotify limits, free disk, SQLite version and tokenizer
behaviour, CPU/RAM/GPU, Python versions, absence of `uv`, and that Ollama is
installed but not runnable by this user (permission denied, service inactive).

Not yet verified, and checked at the start of the phase that needs it:

- `sqlite-vec` loads in the Python build and its version;
- the embedding model's ONNX/GGUF availability and its throughput on this CPU;
- extraction quality on the real PDFs and XLSX files (the ~580 / ~1,290 counts
  are files, not pages or sheets);
- `blake2b` throughput over the corpus (expected ~0.5–1 GB/s, so a full rehash
  is minutes, not hours);
- whether DSH's MCP client accepts the loopback HTTP transport or needs the
  stdio shim.
