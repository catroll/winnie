# Winnie

**A full-featured personal assistant you own: your knowledge, your models, your rules.**

Winnie is being built as a personal assistant that belongs to its user, rather
than a chat window rented from a vendor. The assistant is the product; the
infrastructure below it exists to serve the assistant.

| Layer | What it gives you | Status |
|-------|-------------------|--------|
| **Assistant** | Scheduling, development help and everyday tasks, orchestrated on top of the layers below | Planned |
| **Personal knowledge base** | Markdown in Git as the source of truth: browse, edit, classify, version and search | Planned |
| **Model gateway** | OpenAI- and Anthropic-compatible transparent proxy with per-route upstreams and complete local logs | **Implemented** |

The model gateway is one requirement of the project, not its definition. It
landed first because every later capability needs a vendor-neutral, observable
way to talk to models.

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

The meeting-notes backend is scaffolded but **not production-ready**. Meeting
CRUD, audio upload, a file-first processing pipeline, transcript/minutes/export
APIs and a Markdown knowledge sink are wired. ASR, translation and
summarization currently use deterministic fake providers; real provider
adapters and the Android client are next.

### Quick start

```bash
cd apps/api
uv sync
uv run uvicorn winnie_api.main:app --reload --host 127.0.0.1 --port 8000
```

Then check the proxy:

```bash
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/routes
```

Point any OpenAI-compatible client at `http://127.0.0.1:8000/<route>` and pass
your vendor key in the request headers. Routing, configuration and request
examples are in [docs/openai-proxy.md](./docs/openai-proxy.md).

The meeting API is mounted under `/api/v1/meetings` only when
`meetings.enabled: true` is set in `apps/api/config.yaml`. The integration
design and endpoint list are in [docs/meetings.md](./docs/meetings.md).

## Roadmap

1. **Meeting notes** — real ASR, translation and summarization adapters; the
   Android client; realtime streaming; then cross-meeting search.
2. **Knowledge base** — Git-backed Markdown, management UI, classification and
   tagging, re-indexing, retrieval-augmented chat.
3. **Assistant capabilities** — scheduling, development assistance and everyday
   tasks, composed over the knowledge base and the gateway.
4. **Production readiness** — user isolation and permissions, queue-backed
   background work, performance and stability.

Details and design rationale are in [DESIGN.md](./DESIGN.md).

## Tech stack

Target stack; the API, SQLAlchemy models and inline job queue scaffold are in
place.

| Area | Choice |
|------|--------|
| Language and API | Python, FastAPI |
| Management UI | Vite + React |
| Agent orchestration | LangGraph (pipeline is plain async Python for now) |
| Knowledge source | Git-managed Markdown |
| Vector index | Qdrant |
| Async jobs | Redis + arq (inline queue for local development) |
| Model access | LiteLLM over OpenAI-compatible APIs |

## Documentation

- [DESIGN.md](./DESIGN.md) — goals, layers, knowledge model, architecture and roadmap
- [EVAL.md](./EVAL.md) — living feasibility assessment
- [docs/openai-proxy.md](./docs/openai-proxy.md) — gateway contract, configuration and examples
- [docs/meetings.md](./docs/meetings.md) — meeting-notes integration design
- [apps/api/README.md](./apps/api/README.md) — backend layout, running and tests

## License

[Apache License 2.0](./LICENSE).
