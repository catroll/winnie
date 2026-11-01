# winnie-index

A local index over the owner's `.md` and `.txt` files, with three ways to reach
it: an Agent Skill that declares the tool (for DSH, Cursor and Claude Code), an
MCP server (loopback HTTP, plus a stdio shim) and a small HTTP API (for Winnie's
assistant).

## Why it exists

An agent should be able to answer "what did I write about X" without being told
which directories exist. The index is derived from the files, never authoritative:
delete `~/.local/share/winnie-index/` and the next sweep rebuilds it.

## Quick start

```bash
# 1. A config with at least one root
python -m winnie_index.cli init          # writes ~/.config/winnie-index/config.yaml
$EDITOR ~/.config/winnie-index/config.yaml

# 2. See what would be indexed (writes nothing)
python -m winnie_index.cli build --dry-run

# 3. Index it
python -m winnie_index.cli build

# 4. Ask
python -m winnie_index.cli search 部署
python -m winnie_index.cli get project.md --lines 20
python -m winnie_index.cli status
```

## Surfaces

| Surface | How |
|---|---|
| Agent Skill | `python -m winnie_index.cli skill install` → `~/.agents/skills/local-knowledge/SKILL.md` |
| MCP over HTTP | `python -m winnie_index.cli serve` → `POST http://127.0.0.1:8790/mcp` |
| MCP over stdio | `python -m winnie_index.cli mcp --stdio` |
| HTTP API | `POST /search`, `GET /doc?path=…`, `GET /status`, `GET /healthz` |

The skill only declares the MCP and HTTP surfaces; it never tells an agent which
directories exist.

## Behaviour worth knowing

- **Freshness.** A sweep compares `stat` before reading, a byte hash before
  decoding, and a text hash before re-chunking. `get` revalidates against the
  file, so a read never returns stale text even if the sweep lagged. A writer
  that knows which file changed posts `{"path": …}` to `/admin/index` instead of
  waiting up to `scan.interval_seconds`.
- **Phrase, then OR.** A CJK query is one FTS5 phrase of adjacent bigrams, so a
  paraphrase finds nothing; `{"loose": true}` retries the same tokens as an OR
  and marks every hit `"loose": true`. The strict behaviour is the default, so a
  caller that draws a conclusion from "nothing found" must opt in to the
  forgiving variant and can see that it did.
- **Dedupe.** Identical bytes share one decode and one set of chunks; search
  returns one result per text with the other paths in `also_at`.
- **Chinese.** FTS5's own tokenizers treat a whole Chinese run as one token, so
  text is pre-tokenised into overlapping bigrams before indexing.
- **Sealed, not guessed.** A binary file, a non-decodable encoding or a file
  over `index.max_file_mb` is recorded as `sealed` with a reason, never indexed
  as mojibake.

## Admin API

The daemon exposes management endpoints for the Winnie console. They are
loopback-only and reject a browser request whose `Origin` is not loopback, so a
random page cannot rewrite the configuration.

| Endpoint | Purpose |
|---|---|
| `GET /admin/status` | Coverage, roots, failures, endpoints, skill state |
| `GET /admin/config` | Editable configuration (not `data_dir` or `serve`) |
| `PUT /admin/config` | Validate, write the YAML, reload in place; removes retired roots |
| `POST /admin/search` | Chunk-level hits, the FTS expression and a miss hint |
| `POST /admin/build` | `{"dry_run": true}` to preview, false to index |
| `POST /admin/index` | `{"path": "/abs/file.md"}` to index one file now, without waiting for the sweep |
| `POST /admin/skill` | `{"action": "install"|"check"}` |

## Service

`deploy/winnie-index.service` is a `systemctl --user` unit template. Adjust the
`PYTHONPATH` to the checkout, then:

```bash
loginctl enable-linger "$USER"        # survive logout
systemctl --user enable --now winnie-index.service
journalctl --user -u winnie-index -f
```

## Tests

```bash
PYTHONPATH=packages/winnie-index/src apps/api/.venv/bin/python -m pytest packages/winnie-index/tests -q
```

## Not implemented yet

Dense vectors (the session currently uses BM25 only), inotify acceleration, the
reranker, and the evaluation fixture. The design and its reasons are in
[`docs/knowledge-index.md`](../../docs/knowledge-index.md).
