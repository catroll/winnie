# Winnie

**A full-featured personal assistant you own: your knowledge, your models, your rules.**

Winnie is being built as a personal assistant that belongs to its user, rather
than a chat window rented from a vendor. The assistant is the product; the
infrastructure below it exists to serve the assistant.

| Layer | What it gives you | Status |
|-------|-------------------|--------|
| **Assistant** | Scheduling, development help and everyday tasks, orchestrated on top of the layers below | Planned |
| **Meeting notes** | File-first transcription with live captions, cross-lingual transcript and structured minutes | Backend implemented; live ASR validated; device run pending |
| **Android client** | Record with live captions, upload, read transcript and minutes | **MVP implemented** |
| **Management console** | Web view of meetings, transcripts, minutes and gateway routes | **MVP implemented** |
| **Accounts** | Local users, Argon2id passwords and opaque bearer sessions | **Implemented** |
| **Personal knowledge base** | Markdown in Git as the source of truth: browse, edit, classify, version and search | Planned |
| **Model gateway** | OpenAI- and Anthropic-compatible transparent proxy with per-route upstreams and complete local logs | **Implemented** |

The model gateway is one requirement of the project, not its definition. It
landed first because every later capability needs a vendor-neutral, observable
way to talk to models.

Milestone status, completion evidence and the next development plan are in
[docs/plan.md](./docs/plan.md).

## Why Winnie

- **No vendor lock-in.** Models are routes in a configuration file. Switching
  providers does not force a change to your data or to how you work.
- **Documents are the source of truth.** Long-term knowledge lives as Markdown
  in Git, so every change has an author, a timestamp, a diff and a rollback. The
  vector index is derived and rebuildable, never authoritative.
- **Every interaction is kept.** The gateway records each request and response
  locally, so conversations can be revisited and distilled into knowledge
  instead of disappearing.
- **You can intervene.** Knowledge is readable, editable and revertible;
  automatic classification and retrieval yield to manual correction.
- **Retrieval is explainable.** An answer can point back to the knowledge
  entries it was built from.

## What works today

The model gateway is fully implemented. It supports:

- OpenAI Chat Completions and Anthropic Messages, forwarded without protocol
  translation
- Path-based routing, so one deployment can front OpenAI, Anthropic, DeepSeek
  and Qwen at once
- Client-supplied vendor API keys — Winnie never stores them
- Streaming (SSE) passthrough
- Paired `ask`/`answer` logs on local disk, per route

The meeting-notes backend is functional but **not production-ready**. Meeting
CRUD, audio upload, a file-first processing pipeline, transcript/minutes/export
APIs and a Markdown knowledge sink are wired. Accounts, per-user meeting
isolation, the Volcengine Doubao ASR adapter, and OpenAI-compatible translation
and structured-minutes adapters are implemented. The fake providers remain the
default for tests and local demos. The DeepSeek translation/minutes smoke test
passed, and the file and streaming Doubao ASR paths are validated against the
real endpoints. The Android client MVP in `apps/android` runs the file-first
flow with live captions — sign in, record, watch provisional captions, stop,
upload, transcript and minutes — and is verified against the local backend;
running it on a device with a live ASR key is still pending.

The AdminLTE management console in `apps/admin` operates the same data in a
browser: sign in, dashboard, meeting list and detail, transcript and minutes,
Markdown/TXT export, regenerate and delete, gateway route inspection, and
account/password management. It is read-and-operate only — recording and upload
stay in the Android client.

### Quick start

```bash
make api        # [::]:8000, reachable over IPv6, reload enabled
make stop       # stop the API listening on API_PORT
make restart    # stop, then start again with the current environment
make android    # debug APK for the Android client
make admin      # admin console at http://127.0.0.1:5173
make help       # targets and overridable variables
```

`make api` uses `uv` and falls back to `apps/api/.venv` when `uv` is not on
`PATH`; run `cd apps/api && uv sync` first on a fresh checkout. It sources
`apps/api/.env` when that file exists — gitignored, and only a local
convenience: the application itself reads secrets from the process environment.
That file also supplies `WINNIE_PUBLIC_URL`, the default server address baked
into the Android APK; start from `apps/api/.env.example`. `make android` needs a
JDK and the Android SDK; see
[apps/android/README.md](./apps/android/README.md). `make admin` needs the
console's dependencies first: run `cd apps/admin && npm install`; see
[apps/admin/README.md](./apps/admin/README.md).

Then check the proxy:

```bash
curl -s 'http://[::1]:8000/healthz'
curl -s 'http://[::1]:8000/routes'
```

Point any OpenAI-compatible client at `http://[::1]:8000/<route>` (or
`API_HOST=0.0.0.0 make api` for IPv4) and pass your vendor key in the request
headers. Routing, configuration and request examples are in
[docs/openai-proxy.md](./docs/openai-proxy.md).

Accounts and meeting notes are mounted under `/api/v1` when enabled in
`apps/api/config.yaml`. The first account can register, then login to receive a
bearer token. To transcribe with Doubao, export
`VOLCENGINE_ASR_API_KEY` (the API key from the current Volcengine console)
before starting the API. To use a real model for translation and minutes, set
`meetings.translator` and `meetings.summarizer` to `openai_compatible` and
export `WINNIE_LLM_API_KEY`. The integration design and endpoint list are in
[docs/meetings.md](./docs/meetings.md).

## Roadmap

1. **Meeting notes** — validate and tune the Doubao and LLM provider paths on
   real recordings; evaluate a second ASR vendor on bilingual audio; run the
   Android client on a device; foreground-service live captions and reconnect;
   then cross-meeting search.
2. **Knowledge base** — Git-backed Markdown, knowledge-base UI, classification
   and tagging, re-indexing, retrieval-augmented chat.
3. **Assistant capabilities** — scheduling, development assistance and everyday
   tasks, composed over the knowledge base and the gateway.
4. **Production readiness** — migrations, queue-backed background work,
   retention and backups, deployment hardening.

Details and design rationale are in [DESIGN.md](./DESIGN.md). Concrete follow-ups,
including the ASR evaluation and provider verification, are in
[docs/todo.md](./docs/todo.md).

## Tech stack

| Area | Choice |
|------|--------|
| Language and API | Python, FastAPI |
| Data | SQLAlchemy 2 async; SQLite locally, PostgreSQL-ready |
| Accounts | Argon2id passwords, opaque bearer sessions |
| Meeting storage | Local disk behind a `Storage` port |
| ASR | Volcengine Doubao (big-model recording file) |
| Translation and minutes | OpenAI-compatible chat model via an internal LLM client |
| Management UI | Vite + React + AdminLTE 4 |
| Android client | Kotlin, Jetpack Compose, OkHttp, kotlinx.serialization |
| Knowledge source | Git-managed Markdown |
| Vector index | Qdrant |
| Async jobs | Redis + arq (inline queue for local development) |
| Model access | LiteLLM over OpenAI-compatible APIs |

## Documentation

- [DESIGN.md](./DESIGN.md) — goals, layers, knowledge model, architecture and roadmap
- [EVAL.md](./EVAL.md) — living feasibility assessment
- [docs/openai-proxy.md](./docs/openai-proxy.md) — gateway contract, configuration and examples
- [docs/meetings.md](./docs/meetings.md) — meeting-notes integration design
- [docs/todo.md](./docs/todo.md) — prioritized follow-ups and ASR evaluation plan
- [docs/knowledge-index.md](./docs/knowledge-index.md) — local document index: freshness contract, schema and phased plan
- [apps/api/README.md](./apps/api/README.md) — backend layout, running and tests
- [apps/android/README.md](./apps/android/README.md) — Android MVP: build, run and limitations
- [apps/admin/README.md](./apps/admin/README.md) — management console: pages, serving and security

## License

[Apache License 2.0](./LICENSE).
