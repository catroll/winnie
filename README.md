# Winnie

**A full-featured personal assistant you own: your knowledge, your models, your rules.**

Winnie is being built as a personal assistant that belongs to its user, rather
than a chat window rented from a vendor. The assistant is the product; the
infrastructure below it exists to serve the assistant.

| Layer | What it gives you | Status |
|-------|-------------------|--------|
| **Assistant** | Chat, web search, scheduled tasks, long-term memory and opt-in local file/command tools | **Core implemented** |
| **Meeting notes** | File-first transcription with live captions, cross-lingual transcript and structured minutes | Backend implemented; live ASR validated; device run pending |
| **News production** | Seven-stage pipeline from public sources to a reviewed draft and a dated digest, with API, console and assistant access | **Core implemented** |
| **Android client** | Assistant chat with Markdown, on-device "winnie" wake word into a hands-free voice turn, 5-second change-checked phone sync, record with live captions, reminders, remembered sign-in, English/Chinese UI | **MVP implemented; wake word not yet measured on a device** |
| **Phone data** | Calendar, call log, SMS, contacts, notifications, location, installed apps and device info synced to the server; confirmed calendar and SMS writes | **Core implemented; device run pending** |
| **Push notifications** | Assistant reminders, scheduled briefs and phone-action results arrive on the phone through a self-hosted stream | **Core implemented; device run pending** |
| **Proactive assistance** | Notices birthdays and work items without being asked, decides whether to interrupt, says what to do, and escalates until acknowledged; the console lists every item it sent and why | **Core implemented; physical-device run pending** |
| **Management console** | Web view of meetings, transcripts, minutes, assistant chat — typed or as a hands-free voice conversation with spoken replies — and cross-channel conversation history, synced phone data, the weekly schedule, scheduled tasks, news runs, drafts and digests, AI providers (suppliers and LLM usage) and gateway routes | **MVP implemented** |
| **Accounts** | Local users, Argon2id passwords and opaque bearer sessions | **Implemented** |
| **Personal knowledge base** | Markdown in Git as the source of truth: browse, edit, classify, version and search | Planned |
| **Personal state** | Where you are, what you are doing and whether you slept — sensed, carried with a freshness stamp, never invented | Planned (proposal) |
| **Room senses** | Cameras and microphones as eyes and ears: ambient detection feeding `look`/`listen`/`speak` | Planned (proposal) |
| **Document index** | Local `.md`/`.txt` index served to agents over MCP and HTTP, with an Agent Skill that declares both | **v1 implemented (lexical)** |
| **Model gateway** | Byte-level OpenAI- and Anthropic-compatible proxy with per-route upstreams, Winnie gateway keys and complete local logs | **Implemented** |
| **LLM usage** | Per-call tokens and latency, cost estimates and provider balances on the console's AI providers page | **Implemented** |

The model gateway is one requirement of the project, not its definition. It
landed first because every later capability needs a vendor-neutral, observable
way to talk to models.

Milestone status, completion evidence and the next development plan are in
[docs/plan.md](./docs/plan.md).

## Why Winnie

- **No vendor lock-in.** AI vendors are integrations and each feature selects
  one in the console. Switching the assistant, translation, minutes or ASR does
  not change your data or how you work.
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

- OpenAI and Anthropic wire protocols forwarded byte for byte — every path and
  method, query string and header, not a hand-written endpoint list
- Path-based routing, so one deployment can front OpenAI, Anthropic, DeepSeek
  and Qwen at once
- Winnie gateway keys: owned per account, route-scoped and revocable, managed on
  the console's API keys page
- Server-held vendor credentials — clients never see a vendor key
- Streaming (SSE) passthrough
- Paired `ask`/`answer` logs on local disk, per route
- A usage ledger shared with internal model calls, attributed to the key that
  made the call, priced per model, plus provider balances where the vendor
  exposes them

The meeting-notes backend is functional but **not production-ready**. Meeting
CRUD, audio upload, a file-first processing pipeline, transcript/minutes/export
APIs and a Markdown knowledge sink are wired. Accounts, per-user meeting
isolation, the Volcengine Doubao ASR adapter, and OpenAI-compatible translation
and structured-minutes adapters are implemented. The deterministic fakes are
test-only; no fake supplier ships in the console. The DeepSeek
translation/minutes smoke test
passed, and the file and streaming Doubao ASR paths are validated against the
real endpoints. The Android client MVP in `apps/android` runs the file-first
flow with live captions — sign in, record, watch provisional captions, stop,
upload, transcript and minutes — alongside the assistant chat tab, and is
verified against the local backend; running it on a device with a live ASR key
is still pending.

The assistant capability is usable end to end: a native chat tab in the Android
client, a phone-sized web page at `/api/v1/assistant/ui` and a ChatGPT-like chat
in the management console, a dedicated `winnie-chat` engine with a skill
registry (web, weather, document index, memory, scheduled tasks and local
execution), append-only Markdown conversations under `~/Documents/Mine/Winnie`,
long-term memory distilled from those files and recalled through the local index
service, and scheduled tasks the assistant creates and cancels when asked in
conversation. The Android tab renders replies as Markdown and shares one
conversation history with the console and the web page; tool output stays
collapsed until it is opened. Two watches are on
by default: a daily weather check records an umbrella or clothing notice only
when the advice changes, and a gold watch samples the price every five minutes
and pushes the Winnie app when it moves sharply (≥1% in an hour or ≥2.5% in a
day). Their skills are available in chat, and the console's Skills page is the
feature list that can switch any skill off. The assistant can also list,
read and write files under a configured workspace and run local commands after
an LLM review and an explicit confirmation; the file and command tools are
audited, whitelisted read-only operations skip the review, and the command
runner is **not** a sandbox. Every conversation
records its channel, so the console history also covers the phone page, direct
API clients and unattended task runs. No model is configured by default: add a
supplier API and bind the assistant to one of its models on the AI suppliers
page. Design, decisions and operating notes are in
[docs/assistant.md](./docs/assistant.md); the index is in
[docs/knowledge-index.md](./docs/knowledge-index.md).

The phone-data capability in `packages/winnie-phone` turns the phone into the
assistant's sensor: the Android client uploads the scopes its owner enabled
(calendar, call log, SMS, contacts, notifications, location every five minutes,
installed apps and device info), the server merges by device and record id, and
the assistant reads it through the `phone` skill. While the app process or its
alert service is alive, a 5-second loop checks each scope for local changes and
uploads only what changed — a new SMS sends one row, not a snapshot — and a
15-minute WorkManager worker is the fallback once Android kills the process. Writes go the other way: the
assistant proposes a calendar change or a text, the owner confirms it in chat,
and the phone executes the queued command on its next sync and reports the
result. Nothing is enabled by default, every scope has its own permission, and
the server stores the data in clear text with per-kind retention. The client
builds and its unit tests pass; a run on a physical phone is still pending.
Design, contract, retention and limits are in [docs/phone.md](./docs/phone.md).

The push capability in `packages/winnie-push` lets Winnie reach the owner
without the phone polling: the server stores each notification before it sends
it, streams it over a WebSocket held by an Android foreground service, and
replays the backlog after a reconnect. The assistant can send a notice with a
`notify_user` tool, every scheduled task pushes its brief, and a confirmed phone
command wakes the phone to execute immediately and reports the result. There is
no vendor push service; the cost is a persistent low-priority notification and
some battery use. Design, protocol, privacy and limits are in
[docs/push.md](./docs/push.md).

The news pipeline in `packages/winnie-news` collects RSS, Tencent and tophub
material, cleans and extracts it, groups reports of one event, searches for
missing coverage, writes one draft per event and reviews it — returning concrete
feedback to the writer at most twice before marking a draft for a person. The
drafts that pass are then assembled into a dated briefing (four sections, three
formats) without another model call. A run, its stage progress, every draft and
the digest (JSON and Markdown) live under `data/news`; the console's News page
and the assistant's `news` skill operate the same service, and the three model
purposes resolve through the AI providers page. The draft contract — about 200
characters, tags, a position kept intact, no source apparatus in the text — is
the owner's editor plugin, quoted verbatim into the prompts and enforced in code
as well. It was verified end to end against real RSS sources with the fake
model; a real writing model on a full batch is still unmeasured. Design,
contracts and limits are in [docs/news.md](./docs/news.md).

The AdminLTE management console in `apps/admin` operates the same data in a
browser: sign in, dashboard, assistant chat with the full cross-channel
conversation history, chat skills, meeting list and detail, transcript and
minutes, Markdown/TXT export, regenerate and delete, the phone data the Android
client synced (devices, per-kind records, search, assistant actions, revoke and
delete), assistant scheduled tasks and their run history, AI provider CRUD with
per-feature provider selection, gateway route inspection, and
account/password management. It is read-and-operate for
recordings — upload stays in the Android client, and scheduled tasks are created
and cancelled through the assistant chat. AI vendors and their feature bindings
are the exception: they are owned by the console. See
[docs/ai-providers.md](./docs/ai-providers.md).

### Quick start

```bash
make start      # start API, index and admin in the background
make status     # port, state and PID for each service
make stop       # stop all managed services
make restart    # stop, then start all managed services
make android    # debug APK for the Android client
make news-fetch # collect news material to /tmp/winnie-news-material.json
make help       # targets and overridable variables
```

The full local stack is three services: the API on `[::]:8000`, the document
index on `127.0.0.1:8790`, and the admin console on `127.0.0.1:5173`. Ports,
logs, optional worker/Redis/PostgreSQL and production shape are documented in
[docs/operations.md](./docs/operations.md).

`make api`, `make index-serve` and `make admin` run one service in the
foreground for development. `make api` uses `uv` and falls back to
`apps/api/.venv` when `uv` is not on `PATH`; run `cd apps/api && uv sync` first
on a fresh checkout. It sources
`apps/api/.env` when that file exists — gitignored, and only for local
non-secret settings: it supplies `WINNIE_PUBLIC_URL`, the default server address
baked into the Android APK; start from `apps/api/.env.example`. `make android`
needs a JDK and the Android SDK; see
[apps/android/README.md](./apps/android/README.md). `make admin` needs the
console's dependencies first: run `cd apps/admin && npm install`; see
[apps/admin/README.md](./apps/admin/README.md).

Then check the proxy:

```bash
curl -s 'http://[::1]:8000/healthz'
curl -s 'http://[::1]:8000/routes'
```

Point any OpenAI-compatible client at `http://[::1]:8000/<route>` (or
`API_HOST=0.0.0.0 make api` for IPv4) and authenticate with a Winnie gateway
key, created on the console's API keys page. The supplier key is injected by
the server from the AI registry, where it is stored with the supplier's models;
clients never hold it. Routing, configuration and request examples are in
[docs/openai-proxy.md](./docs/openai-proxy.md).

Accounts and meeting notes are mounted under `/api/v1` when enabled in
`apps/api/config.yaml`. The first account can register, then login to receive a
bearer token. Every model vendor call — chat, transcription and live captions — is made by
`packages/winnie-llm`, and every supplier key is stored with its models in the
AI registry (`data/ai.yaml`, gitignored, mode `0600`). No other module stores a
key. To transcribe with Doubao, set the `volcengine` supplier's key on the AI
providers page; to use a real model for the assistant, translation or minutes,
add an OpenAI-compatible provider there and set its key too. The console also
mirrors every key into `packages/winnie-llm/.env` (gitignored; start from
`packages/winnie-llm/.env.example`) as a backup, so no restart is needed after
a change.
Provider selection and first-start behaviour are in
[docs/ai-providers.md](./docs/ai-providers.md); the meeting integration design
and endpoint list are in [docs/meetings.md](./docs/meetings.md).

## Roadmap

1. **Meeting notes** — validate and tune the Doubao and LLM provider paths on
   real recordings; evaluate a second ASR vendor on bilingual audio; run the
   Android client on a device; foreground-service live captions and reconnect;
   then cross-meeting search.
2. **Knowledge base** — Git-backed Markdown, knowledge-base UI, classification
   and tagging, re-indexing, retrieval-augmented chat.
3. **Assistant capabilities** — first slice done: chat, web search, memory and
   scheduled tasks. Streaming, task creation from chat, vector retrieval and a
   worker for 24/7 task execution remain.
4. **News production** — first slice done: material to reviewed draft and a
   dated digest, console page and assistant skill. A worker-backed run, a digest
   surface in the console and the assistant, a per-request digest window and a
   real-model quality comparison remain.
5. **Phone data** — first slice done: sync, query, assistant tools and the
   confirmed write path. A device run, a console page, a command poll separate
   from the data sync and SMS delivery receipts remain.
6. **Push notifications** — first slice done: durable backlog, WebSocket stream,
   the notify tool, scheduled-task and phone-command events, and the Android
   foreground service. A device run, Redis fan-out for multiple workers,
   notification actions and more event sources remain.
7. **Production readiness** — migrations, queue-backed background work,
   retention and backups, deployment hardening.
8. **State & senses** — sense where the owner is, what they are doing and
   whether they slept (phone, PC agent, watch); room cameras and microphones as
   eyes and ears. Proposed in [docs/state.md](./docs/state.md) and
   [docs/senses.md](./docs/senses.md), sequenced after the knowledge base.

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
| Android client | Kotlin, Jetpack Compose, OkHttp, kotlinx.serialization, WorkManager |
| Phone data | SQLAlchemy tables in `winnie-phone`, WorkManager sync, notification listener |
| Knowledge source | Git-managed Markdown |
| Vector index | Qdrant |
| Async jobs | Redis + arq (inline queue for local development) |
| Model access | LiteLLM over OpenAI-compatible APIs |
| News pipeline | Python package, stdlib collectors, JSON file store |

## Documentation

- [DESIGN.md](./DESIGN.md) — goals, layers, knowledge model, architecture and roadmap
- [docs/plan.md](./docs/plan.md) — project goals, milestones, completion status and next steps
- [docs/operations.md](./docs/operations.md) — service inventory, ports, lifecycle and production shape
- [EVAL.md](./EVAL.md) — living feasibility assessment
- [docs/openai-proxy.md](./docs/openai-proxy.md) — gateway contract, configuration and examples
- [docs/usage.md](./docs/usage.md) — what the usage ledger records, pricing, provider balances and limits
- [docs/meetings.md](./docs/meetings.md) — meeting-notes integration design
- [docs/news.md](./docs/news.md) — news pipeline: stages, contracts, configuration and limits
- [docs/phone.md](./docs/phone.md) — phone data: sync contract, write path, privacy and limits
- [docs/state.md](./docs/state.md) — personal state: what is measurable, the hardware options and the proactive pipeline (proposal)
- [docs/senses.md](./docs/senses.md) — room cameras and microphones: architecture, hardware, security and phases (proposal)
- [docs/push.md](./docs/push.md) — push: transport choice, protocol, foreground service and limits
- [docs/todo.md](./docs/todo.md) — prioritized follow-ups and ASR evaluation plan
- [docs/knowledge-index.md](./docs/knowledge-index.md) — local document index: freshness contract, Skill/MCP/HTTP surfaces and phased plan
- [docs/brand.md](./docs/brand.md) — the woodpecker mark: app icon and console icon geometry, palette and regeneration
- [packages/winnie-index/README.md](./packages/winnie-index/README.md) — the index service: commands, surfaces and limits
- [apps/api/README.md](./apps/api/README.md) — backend layout, running and tests
- [apps/android/README.md](./apps/android/README.md) — Android MVP: build, run and limitations
- [apps/admin/README.md](./apps/admin/README.md) — management console: pages, serving and security

## License

[Apache License 2.0](./LICENSE).
