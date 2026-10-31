# Winnie project plan and status

Last updated: 2026-11-01.

This is the single high-level view of what Winnie is trying to become, how the
work is sequenced, and what is actually finished. Deeper material stays in the
linked documents: [meeting-notes design](./meetings.md),
[backlog](./todo.md) and [decisions](./decisions.md).

Status values used below:

- **Done** — implemented and verified end to end.
- **Core done** — the main path works and is verified; hardening or a named
  validation is still open.
- **Partial** — some pieces exist, but the capability is not usable end to end.
- **Planned** — not started.

## 1. Product goal

Winnie is a personal assistant that belongs to its user. The user owns the
knowledge, chooses the models, and keeps the rules; Winnie is not a chat window
rented from a vendor.

The assistant is the product. The model gateway, meeting notes, accounts and
knowledge base exist to serve it. A capability is only useful if its data can
later be read, corrected and used by the assistant.

Success for the current phase:

1. Capture a meeting end to end: record, see live captions, get an accurate
   transcript, translate it, produce structured minutes, and write a Markdown
   note.
2. Keep every model call observable, replaceable and attributable to a route or
   provider.
3. Keep the resulting knowledge readable, editable, diffable and revertible.

## 2. Scope and principles

### In scope

- One self-hosted backend and one Android client, for one user or a small
  trusted group.
- File-first meeting transcription as the canonical accuracy path, with live
  captions as a provisional view while recording.
- Provider ports behind stable interfaces: ASR, translation, summarization,
  and the OpenAI/Anthropic gateway.
- A personal knowledge base whose source of truth is Markdown in Git.
- Local observability: request/response logs, usage records, health checks.

### Out of scope for now

- Multi-tenant SaaS, billing, quotas and a multi-tenant admin console. The
  single-user operator console in `apps/admin` is in scope.
- A Go BFF or a second independently deployed service.
- Speaker diarization, real-time translation of live captions, and mobile
  background recording before the current path is validated.
- Hosting vendor credentials on the phone. The Android app never sees them.

### Engineering principles

- **One backend, capability packages.** `apps/api` is the composition root;
  capabilities cannot import the host or each other.
- **One canonical transcript.** The file pipeline owns the final transcript,
  translation and minutes. Live captions are a separate, provisional view.
- **File-first before realtime.** Accuracy and failure recovery are easier to
  prove on a recorded file; live captions reuse the same final pass.
- **Verify by driving the real path.** Provider claims need a real request;
  UI claims need a running app or a real HTTP/WebSocket call.
- **Record decisions where they are made.** Provider choices, deliberate
  non-goals and limitations belong in `docs/decisions.md` or the relevant
  document, not only in a commit message.

## 3. Capability map

| Layer | Capability | Where it lives | Status |
|---|---|---|---|
| Assistant | Chat engine (`winnie-chat`) with web, document index, memory, scheduled-task and local-execution skills | `winnie-assistant` | Core done |
| Knowledge base | Markdown in Git: browse, edit, classify, version, search | `winnie-knowledge` note sink only | Partial |
| Meeting notes | File-first upload, ASR, language detection, translation, minutes, export | `winnie-meetings` | Core done |
| Meeting notes | Live captions while recording | WebSocket + Android recorder | Core done |
| News | Six-stage pipeline from public sources to a sourced, reviewed draft | `winnie-news` + `apps/admin` News page + assistant skill | Core done |
| Client | Android sign-in, record, live captions, transcript, minutes | `apps/android` | MVP done |
| Console | Browser console: assistant chat and conversation history, chat skills, meetings, transcripts/minutes, scheduled tasks, AI providers, LLM usage, gateway routes, account | `apps/admin` | MVP done |
| Accounts | Local users, Argon2id, bearer sessions, throttling, reset | `winnie-core/auth` | Done |
| Gateway | OpenAI/Anthropic-compatible transparent proxy, routes, SSE, logs | `winnie-gateway` | Done |
| Observability | Per-call LLM usage ledger, price-based cost estimates and provider balances | `winnie-core/usage` + `apps/api/usage` | Done |
| Operations | Health/readiness, migrations, retention, inline/arq jobs | `apps/api` | Partial |

## 4. Milestones

| ID | Milestone | Exit criteria | Status |
|---|---|---|---|
| M0 | Foundation | Repository, capability packages, host composition root, Alembic baseline, Makefile entry points | **Done** |
| M1 | Model gateway | OpenAI and Anthropic routes forward without translation; SSE passes through; paired ask/answer logs; live upstream test | **Done** |
| M2 | Accounts | Register/login/logout, opaque bearer sessions, per-user meeting isolation, login throttling, password change and host reset | **Done** |
| M3 | File-first meetings | Real bilingual meeting is recorded, transcribed, translated, summarized, exported and written as a note; retention and jobs verified | **Core done** — pipeline and live ASR work; long bilingual fixture and real LLM minutes still pending |
| M4 | Live captions | PCM stream, live caption UI, canonical file pass, foreground recording and reconnect | **Core done** — captions and final pass work; foreground service, reconnect and streaming usage records are open |
| M5 | Knowledge base | Markdown in Git is the source of truth; browse/edit/classify/version/search; notes are indexed and retrievable | Planned |
| M6 | Assistant | A reachable assistant that chats, searches, runs conversation-managed tasks and remembers from local files through the index | **Core done** — chat, search, index-backed recall and chat-managed tasks work; streaming, vector retrieval and an out-of-process worker are open |
| M8 | News production | A run over real sources groups reports into events, writes one sourced draft per event, reviews it and stops at a reviewed Markdown file; console and assistant operate it | **Core done** — end-to-end verified with the fake model; real-model quality, a worker-backed run and Markdown rendering are open |
| M7 | Production readiness | TLS, PostgreSQL, Redis worker, reverse proxy, backups with a restore rehearsal, deployment guide | Partial |

The current work is **M3/M4 hardening** plus the first slice of **M6**: the
meeting capability is usable on the happy path, and the assistant now has a
working chat, search, memory and scheduling path. **M8** added a second content
capability on the same foundations — the news pipeline uses the AI registry,
the assistant's search provider and the console, and ends in a reviewed file
rather than a published post. Remaining work in all three is validation and
durability rather than new surface area.

## 5. Completion by capability

### 5.1 Model gateway — Done

- OpenAI Chat Completions and Anthropic Messages are forwarded as-is; no
  protocol translation.
- Path-based routes can front OpenAI, Anthropic, DeepSeek and Qwen at once.
- Client-supplied vendor keys pass through; Winnie does not store them.
- SSE streaming is proxied; each ask/answer pair is recorded locally.
- Covered by `apps/api/tests/test_gateway.py`.

### 5.2 Accounts — Done

- Argon2id password hashes; only a SHA-256 hash of the session token is stored.
- Sessions expire after `auth.session_ttl_days`; password change and host reset
  revoke every session.
- First account can always register; later registration is controlled by
  `auth.allow_registration`.
- Login throttling is per normalized username and returns `429` with
  `Retry-After`.
- Android stores the bearer token encrypted with a Keystore-held AES/GCM key.
- Covered by `apps/api/tests/test_auth.py` and the Android token tests.

### 5.3 File-first meeting notes — Core done

Working:

- Meeting CRUD, 16 kHz mono WAV upload, local storage with a size limit.
- Volcengine Doubao recording-file ASR 2.0 (`volc.seedasr.auc`), with the
  original + translated segment model.
- Script-based language detection when the provider omits a per-utterance
  label; short segments inherit their neighbour.
- OpenAI-compatible translation and structured-minutes adapters, including
  chunking/map-reduce for long transcripts.
- Transcript, minutes, regenerate, Markdown/TXT export, audio retention and
  delete.
- Markdown knowledge note sink (summary, topics, decisions, action items).
- Inline and arq job backends; PostgreSQL migration path and worker path have
  been rehearsed.
- Usage records for file ASR, translation and summarization.

Verified:

- Live Doubao file ASR returns segments for Mandarin and English.
- A real meeting recording (`你好。`) reached `ready` with one segment.
- Minutes and transcript endpoints returned `200`.

Open:

- A 10–30 minute bilingual fixture (Chinese, English, code-switching, names,
  numbers) has not been recorded; provider quality, timestamp drift and cost
  are therefore not measured.
- The current checkout no longer has `translator`/`summarizer` switches in
  `apps/api/config.yaml`. The real LLM adapters have passed a small live
  smoke test but are not enabled here; a real bilingual meeting with
  `openai_compatible` translation/minutes is still pending.

### 5.4 Live captions — Core done

Working:

- `WS /api/v1/meetings/{id}/stream` accepts 16 kHz mono PCM, authenticates with
  the bearer header (or `?token=` for browser clients), and checks meeting
  ownership.
- A Volcengine SAUC 2.0 adapter (`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel`,
  `volc.seedasr.sauc.duration`) returns cumulative captions; the vendor key
  stays on the server.
- The Android recorder sends 100 ms frames while writing the WAV and shows
  provisional captions under the timer.
- Stopping uploads the same WAV and runs the file pipeline; the final transcript
  replaces the provisional text. A failed stream is a caption failure, not a
  lost recording.

Verified:

- A real stream produced `ready` → `你好` → `你好。` → `final`.
- A meeting created with `source=realtime` accepted the WAV and reached `ready`
  through the file pass.
- The WebSocket auth/ownership and fake-provider paths are covered by
  `apps/api/tests/test_realtime_stream.py`.

Open:

- Foreground-service recording and reconnect after a dropped socket.
- Streaming ASR usage is not written to `usage_records`, so the cost view only
  counts the file pass. A realtime meeting currently pays for two ASR calls.
- The live-caption build has not yet been run on a physical phone.

### 5.5 Android client — MVP done

Working:

- Sign in or create the first account; server address is editable and persisted.
- Record 16 kHz mono PCM to a WAV, with live captions over the meeting stream.
- Meeting list, transcript with original + translation, structured minutes.
- Discarding a recording deletes the draft meeting; finishing keeps it.

Verified:

- JVM unit tests pass, including the API client, WAV header and ws:// URL
  derivation.
- The debug APK builds at
  `apps/android/app/build/outputs/apk/debug/app-debug.apk`.
- The file flow has been exercised from a physical phone against the LAN API
  (login, create, upload, transcribe); the first attempt exposed the missing
  server ASR key, and the same meeting reached `ready` after the key was fixed.

Open:

- Run the live-caption build on the phone; the current build predates that run.
- Foreground service, offline cache, export/share, token refresh, speaker
  labels, certificate pinning.
- The meeting language is fixed to `zh` in the Android MVP.

### 5.6 Management console — MVP done

Working:

- AdminLTE 4 console in `apps/admin`, built with Vite, React and TypeScript; no
  separate backend service.
- Password sign-in through `/api/v1/auth/login`, session validation through
  `/api/v1/auth/me`, and sign-out through `/api/v1/auth/logout`.
- Dashboard: meeting count, configured routes, API version and readiness, and
  the five most recent meetings.
- Chat: a ChatGPT-like conversation with the assistant, plus the full history
  from every channel (console, phone page, API and scheduled task runs),
  filterable by channel. Any transcript can be reopened, including its tool
  blocks; a task-run record is read-only.
- Meetings: paginated list; detail with stage, processing error, transcript and
  translations, structured minutes, Markdown/TXT export, minutes regeneration
  and deletion.
- News: pipeline readiness and bindings, the run list with stage badges and
  counts, the drafts a run produced with their review status, and the selected
  draft with its review and Markdown. A run is started from this page and polled
  while it is in flight.
- Scheduled tasks: the assistant-created definitions with schedule, active or
  paused state, next and last run, last result and error, prompt, webhook target,
  ignored definitions and the scheduler's timezone. The page is read-only.
- Run history: every recorded execution, newest first, filterable by task and
  paged 25 at a time, with start time, duration, result, conversation id and both
  execution and delivery errors.
- Skills: the registered chat skills with title, description, tools,
  permissions, prompt instructions, availability and scheduled-run safety. The
  switch calls `PUT /assistant/skills/{name}`; the core skill is always on.
- AI suppliers: add, edit and delete suppliers (DeepSeek, OpenAI, Qwen,
  Anthropic, Volcengine), their APIs and model catalogs. The console groups
  endpoints under one supplier, so DeepSeek's gateway/API records do not read as
  three suppliers. Each feature selects a **model**; the supplier and its
  internal API are resolved from that model. The supplier model list is fetched
  with **Sync models** from its `/models` API, and vendor marks come from the WLB
  newicon catalog. This registry is the only model choice; `config.yaml` has no
  `llm`, `assistant.model`, `meetings.translator` or `meetings.summarizer`. A
  fresh registry leaves model features unbound; the owner adds a supplier
  API/model and binds them. Each
  OpenAI/Anthropic endpoint is independently a gateway route or an internal-only
  provider. Changing a binding takes effect on the next model call; adding a
  gateway endpoint adds its route immediately. API keys are environment-variable
  names shown with a set/missing indicator, never key values; a keyless OpenAI
  endpoint is gateway-only and cannot serve an internal feature.
- Model runtime: shared timeout, token, temperature, retry and chunk limits live
  in the registry and are editable in the console; they apply to the next call.
- Gateway routes: protocol, upstream, API base and proxy paths from the AI
  provider registry (or from `config.yaml` when the registry is disabled).
- Account: profile, session expiry and password change. A password change
  revokes every session and returns to login with a notice.
- AdminLTE's own colour-mode control (light/dark/auto) and responsive sidebar.

Boundary:

- Read-and-operate for recordings and tasks: recording and audio upload stay in
  the Android client, and task creation/cancellation stays in the assistant
  chat. The console owns the AI provider registry and feature bindings, and
  writes through the API rather than editing files directly.
- Same-origin by default (Vite proxy in development, reverse proxy in
  production); the API has no CORS middleware.
- The bearer token is kept in `sessionStorage`, or `localStorage` with
  **Remember me**; it is never rendered or sent anywhere except the API.

Verified:

- `npm run build` passes TypeScript 7 and Vite 8.
- A real Firefox session against the API covered login, dashboard, meeting list
  and detail, transcript/minutes, export, regenerate, delete, routes, account,
  password change and re-sign-in, dark mode, the mobile sidebar, and redirect to
  login after the server session was revoked.
- The scheduled-tasks page was checked against a disposable assistant root with
  active, paused, failing and ignored definitions, in light and dark mode.
- The chat page was checked in Firefox against a disposable API and assistant
  root: sign-in, a new console conversation, the four-channel history and its
  filter, a task-run record with no composer, a transcript with a collapsed
  `web_search` block, light and dark mode, and a 390 px viewport with no
  horizontal overflow.
- The AI suppliers page was checked in Firefox against a disposable API and
  registry: a fresh seed left model features unbound; DeepSeek's
  gateway routes were grouped under one supplier; the feature table exposed a
  single model selector (no provider column); model sync/editor, supplier
  icons, API create and the unbound state were exercised; the Model runtime
  card saved new limits; the Volcengine defaults, light and dark mode were
  captured at 1440×1200.
- A `version: 1` registry (combined route + internal LLM) was loaded and checked:
  the migration split it into `deepseek-openai` + `internal-llm`, cleared the
  route key/model, repointed the bindings and wrote `version: 2`.

Open:

- No committed frontend test suite; the browser checks ran against a disposable
  database copy.
- Production serving (reverse proxy plus SPA fallback) is documented in
  `apps/admin/README.md`, but no deployment configuration is checked in.
- The knowledge-base UI remains planned; this console now covers the assistant
  chat, meetings, scheduled tasks, AI providers, routes and the account.
- Chat replies are non-streaming; the page shows a thinking indicator and
  re-reads the file after the reply. SSE is the next additive step.
- Provider credentials stay in the process environment; rotating a key still
  requires an API restart. Entering secrets in the browser was rejected for this
  slice (see [decisions](./decisions.md)).

### 5.7 Knowledge base — Partial

- `winnie-knowledge` writes a Markdown note for finished minutes, with
  summary/decisions/actions only.
- The Git commit/versioning layer, browse/edit/classify/search UI, re-indexing
  and retrieval are not implemented.
- This is the next product milestone after meeting hardening (M5).

### 5.8 Operations — Partial

Working:

- `/healthz` and `/readyz` (database and, when configured, Redis).
- Alembic migrations; `alembic check` is clean; upgrade/downgrade tested.
- Audio retention, session cleanup and standalone CLI jobs.
- `make start`, `make status`, `make stop`, `make restart` manage the API,
  index and admin console as one stack; `make api`, `make index-serve` and
  `make admin` remain foreground development commands.
- Secrets come from the process environment; `make api` loads the gitignored
  `apps/api/.env` as a local convenience.

Open:

- TLS termination; the current Android/API path is cleartext HTTP/WS.
- Production PostgreSQL + Redis deployment and a backup/restore rehearsal.
- Reverse proxy, deployment guide, structured per-stage logs and a cost view.
- The public domain's DNS still contains a stale `A` record; the LAN address is
  the verified path.

### 5.9 Assistant — Core done

Working:

- `POST /api/v1/assistant/chat` runs a bounded tool loop in
  `packages/winnie-chat`; the page at `/api/v1/assistant/ui` is phone-sized and
  needs no build step, and the console at `/chat` is the desktop surface.
- The chat engine selects enabled skills and builds the system prompt from their
  instructions. Six skills are registered: core, web, documents (the local
  index), memory, tasks and local (files/commands). The registry persists
  enable/disable state in `skills/skills.json`; the assistant only creates the
  conversation and records the blocks.
- Local tools are opt-in and live in `packages/winnie-local`:
  `list_files`, `read_file`, `write_file`, `run_command` and `confirm_action`.
  File paths are confined to `assistant.workspace.root` and protected paths;
  overwrites carry the sha256 they read, are backed up and require a later
  explicit confirmation. Every non-whitelisted action gets one LLM review
  (the bound assistant.chat model, fail-closed) before it can become pending; whitelisted
  read-only operations skip it. Commands run as the API user after review and
  confirmation and are audited under `.state/`; they are not sandboxed.
  Scheduled task runs get neither group.
- Each conversation records where it started (`channel`: `admin`, `web`, `api`
  or `task`); the console filters the history by channel and opens task runs
  read-only.
- Files live in `~/Documents/Mine/Winnie`: append-only conversations, profile
  notes per memory category, `tasks/tasks.json` and `tasks/runs.jsonl`. The
  directory is created on startup with a README explaining the layout.
- Recall is served by `winnie-index`: `search_documents` returns ranked hits
  with `path:line`, `read_document` revalidates before returning text. The
  assistant has no index of its own; the pinned profile digest is the only
  memory read in process.
- Memory is distilled from the conversation files with a per-conversation
  watermark, deduplicated, and written back with provenance.
- Scheduled tasks are created, listed and cancelled **in conversation**
  (`schedule_task`, `list_tasks`, `cancel_task`); the model must confirm the
  schedule and content before creating one. Task runs cannot create tasks.
- Web tools: `web_search` (Bing RSS) and `fetch_page` (SSRF-guarded).

Verified:

- A live DeepSeek turn restated a daily 07:30 brief and waited; after "可以" it
  called `schedule_task`, wrote `tasks/tasks.json`, and reported the id and next
  run.
- `remember` wrote `profile/preferences.md`; the index build added the new
  `Winnie/` files, and a later question was answered with
  `Winnie/profile/preferences.md:7` after `search_documents` + `read_document`.
- A manual task run finished in 6.7 s, wrote a task-sourced conversation with
  tool blocks and one `runs.jsonl` line; after a restart the task, next run,
  conversations and profile survived.
- A disposable service with a scripted model exercised the local tools:
  `read_file`/`write_file` worked, a command stayed pending until the next
  "确认" turn, and both audit files were written.
- The `winnie-local` suite covers the whitelist policy (paths, git options,
  path-qualified executables), reviewer JSON/timeout/error handling, redaction,
  whitelisted bypass, review allow/deny, and the fail-closed disabled path.
- `199 passed` in the backend suite.

Open:

- Streaming replies (SSE).
- The scheduler lives in the API process, so tasks do not run while the API is
  down; a separate worker is the next step for true 24/7 behaviour.
- Vector retrieval would grow a dense tier in the index, not in the assistant.
- A task cannot be edited or paused yet; cancel and recreate is the current path.
- Commands are not sandboxed: they run as the API user. A bubblewrap/firejail or
  container sandbox is the next hardening step.
- The LLM reviewer reuses the bound assistant.chat model; a separate reviewer model is a
  follow-up.
- Scheduled task runs deliberately have no file or command tools; there is no
  human turn to confirm them.

### 5.10 News production — Core done

Working:

- Six stages behind versioned JSON contracts: material, clean, aggregate,
  search, generate, review.
- RSS, Tencent hot list and tophub collectors with per-source failure isolation
  and a one-hour cache; a failing source is reported, never fatal.
- Rule filtering before any model call; extraction as a second filter; hard
  aggregation conflicts (type, location, time) decided without a model.
- Event store as a JSON file with indexes rebuilt on load; articles are never
  attached twice.
- Sourced drafts: invented URLs are removed, the writer may cite only input
  URLs, and a number that appears nowhere in the material fails review.
- Bounded generate/review loop, then `needs_human`; run status `done`,
  `partial` or `failed` with per-stage status and counts.
- Three AI bindings (`news.extract`, `news.write`, `news.review`), one search
  provider shared with the assistant, and a deterministic offline model.
- Console News page (run list, stage badges, drafts, review, Markdown) and an
  assistant `news` skill that is scheduled-safe.

Verified:

- A real run over two RSS feeds with the fake model: 60 materials, 10 cleaned,
  10 events, 10 drafts, 10 passing reviews.
- The console page in headless Firefox: sign-in, run table, draft list, review
  and Markdown for a selected draft.
- 81 package tests and 205 API tests, including an API-level run and the
  assistant tool path.

Open:

- A real writing model has not been run over a full batch; draft quality and
  cost are unmeasured.
- A run executes in the API process; a restart leaves its record `running` and
  the `arq` backend does not move news yet.
- Markdown is shown as source, not rendered; a sanitised renderer is a
  follow-up.
- Publication is out of scope: the pipeline ends at a reviewed file.

## 6. Verification snapshot

| Check | Command or path | Result |
|---|---|---|
| Backend suite | `cd apps/api && .venv/bin/python -m pytest -q` | **205 passed** |
| Live assistant chat | DeepSeek + Bing RSS + `weather.com.cn` | Answer with a source URL in 3.0 s |
| Memory distillation | `POST /api/v1/assistant/memory/distill` | One fact written to `profile/preferences.md` |
| Assistant memory recall | `search_documents` + `read_document` through `winnie-index` | Cited `Winnie/profile/preferences.md:7` |
| Tasks created in chat | DeepSeek turn, confirmation, `schedule_task` | `tasks/tasks.json` with model-written prompt and next run 07:30 |
| Task execution | Manual run of the chat-created task | 6.7 s, `ok`, task conversation and one `runs.jsonl` line |
| Live file ASR | Doubao recording-file 2.0 on real audio | `ready`, one segment |
| Live streaming ASR | Doubao SAUC 2.0 via the WebSocket | `ready` → partial → `你好。` → `final` |
| Realtime final pass | `/meetings/{id}/audio` + `/transcribe` on a `realtime` meeting | `uploaded` → `ready` |
| Android JVM tests | `./gradlew testDebugUnitTest` | Passed |
| Android APK | `make android` | `app-debug.apk` built |
| API liveness | `curl http://[::1]:8000/healthz` | `{"status":"ok","version":"0.1.0"}` |
| Admin console build | `cd apps/admin && npm run build` | TypeScript + Vite build passed |
| Admin console flows | Firefox against a disposable API copy | Login, meetings, export/regenerate/delete, routes, account, password change, dark mode, mobile, revoked session passed |
| Admin scheduled-tasks page | Firefox against a disposable assistant root | Active, paused, failing and ignored definitions rendered with schedule, next/last run, error and prompt; run history filtered by task, paged newest first, with errors and unreadable lines; light, dark, mobile and empty states passed |
| Admin chat page | Firefox against a disposable API and assistant root | Login, new console conversation, four-channel history and filter, task record read-only, tool trace, light/dark, 390 px mobile; no horizontal overflow |
| Local assistant tools | Disposable service with a scripted model | `read_file`/`write_file`; command pending until the next "确认" turn; file and command audit lines |
| Local LLM review | `winnie-local` test suite | Whitelisted reads skipped the reviewer; non-whitelisted write/command allowed or denied; malformed JSON, timeout and provider errors denied; denied actions changed nothing |
| Chat engine and skills | `winnie-chat` and API tests | Registry persistence and duplicate rejection; enabled-only schemas and prompt; disabled-tool rejection; scheduled filtering; skills list/toggle/persistence |
| Admin skills page | TypeScript/Vite build + Firefox production preview | Page typechecks and builds; six skills listed with instructions; unavailable index and always-on core; web toggle persisted; light/dark/mobile |
| Admin AI suppliers page | Firefox against a disposable API and `ai.yaml` | Fresh seed left model features unbound; DeepSeek gateway routes were grouped under one supplier; added a model through **Models**; created `ui-llm`, rebound `assistant.chat` (`source=admin`), deleted it and the feature became unbound; saved Model runtime limits; Volcengine defaults, light/dark captured |
| AI registry migration | `pytest` against hand-written v1/v2/v3 files | v1 split into gateway + internal API; v2 grouped under suppliers and collected models; v3 converted bindings to `{vendor, model}`; v4 removed the fake supplier; rewritten `version: 5` |
| News pipeline | `pytest packages/winnie-news/tests` | 81 passed; six stages, fake model, file store, retry and duplicate-attach paths |
| News run end to end | Throwaway API + two real RSS feeds + fake model | 60 materials → 10 cleaned → 10 events → 10 drafts, all reviews passed |
| Admin news page | Firefox against the same disposable API | Sign-in, run table, stage badges, draft list, review and Markdown rendered; no console errors |
| Usage recording | Synthetic OpenAI-compatible upstream through the gateway (`WINNIE_CONFIG=.tmp/usage-e2e/config.yaml`) | 6 real gateway calls (4 ok, 500, 401) plus 12 seeded historical records: 18 calls, 131.71K tokens, `0.0410 USD`, 16 priced / 2 without usage |
| Admin LLM usage page | Firefox against the same disposal API | Period switch, metrics, balances (not-exposed and missing-key states), by-day/by-feature/by-model tables, feature filter (8 gateway calls), detail row without column resize, price table, light/dark and 500 px width with no page overflow |

Not yet verified:

- A clean live-caption run from the installed APK on a physical phone.
- A long bilingual meeting with real LLM translation and minutes.
- TLS, public-domain routing and a production backup/restore.

## 7. Known gaps and risks

| Gap | Impact | Next action |
|---|---|---|
| Two ASR calls per realtime meeting | Double provider cost; streaming usage is invisible | Add streaming usage records; alternatively make the file pass configurable |
| Cleartext HTTP/WS on a public IPv6 domain | Tokens, audio and transcripts cross the network unencrypted | Terminate TLS before real use; narrow the Android network-security config to that domain |
| No foreground service / reconnect | Backgrounding or locking the phone stops recording and captions | Foreground service with a persistent notification; reconnect with bounded retry |
| Current translation/minutes may be unbound | No fake supplier ships; a fresh registry has no model | Bind those features to a supplier model on the AI suppliers page; tune timeouts in Model runtime; run a real bilingual meeting |
| No long bilingual fixture | Provider quality, timestamps and cost are unmeasured | Add a local-only 10–30 minute fixture and an evaluation harness |
| Cost estimates need a hand-maintained price table | A stale or missing price shows as unpriced; peak/off-peak vendor rates are not modelled | Prefer provider-reported costs, keep the table small, review it when a vendor changes prices |
| Knowledge base is only a note sink | Minutes cannot yet be browsed, corrected or retrieved | M5: Git write, index, browse/edit/search |
| Admin console has no automated tests | Its flows are only checked manually | Add a Vitest/Playwright suite when the console grows beyond these pages |
| SQLite + inline jobs in the current checkout | Not a production topology | PostgreSQL, arq worker, reverse proxy, backups |
| Stale domain `A` record | IPv4 to the domain can resolve to a different host | Remove it or set it to the LAN address; verify the router's inbound rules |
| News drafts are only as good as the writer model | Tests use a deterministic test model; real quality is unmeasured | Bind `news.write` to a real model and compare the same batch; tune prompts and review rules |
| A news run is not worker-backed | A restart loses an in-flight run and leaves its record `running` | Move runs into the arq worker and add a recovery sweep |
| No speaker diarization or export/share on Android | Limited usefulness for multi-speaker meetings | Follow-ups after M4 hardening |

## 8. Next development plan

Ordered by dependency and risk, not by novelty:

1. **Finish M4 hardening.**
   - Install the current APK on the phone and run a live-caption meeting over
     the LAN address.
   - Add a foreground service so recording and the stream survive backgrounding.
   - Add reconnect with bounded retry; fail over to the file pass on a dropped
     stream.
   - Write streaming ASR usage records, or extend the LLM usage page with ASR
     audio seconds, or explicitly document the double-cost tradeoff in the UI.
   - Exit criteria: a 10-minute meeting recorded on the phone with live
     captions, screen locked for part of it, ending in a `ready` transcript.

2. **Close M3 validation.**
   - Add the bilingual fixture (local only, never committed).
   - Enable real translation and summarization with `DEEPSEEK_API_KEY`.
   - Record a real meeting, fix timestamp/language/prompt issues, measure
     latency, tokens and cost.
   - Exit criteria: a real bilingual meeting produces a useful transcript,
     translation, minutes and Markdown note, with usage rows.

3. **M5 knowledge base MVP.**
   - Git-backed Markdown write/commit; browse, edit, classify and version.
   - Derived index and retrieval; notes remain the source of truth.
   - Exit criteria: the meeting note is searchable and an answer cites it.

4. **M6 assistant.**
   - Done: chat, web search, index-backed recall, memory distilled from
     conversation files, and tasks created and cancelled in conversation.
   - Next: SSE streaming, vector retrieval in the index, a worker so tasks run
     while the API is down, and task editing.
   - Exit criteria for the slice: a scheduled brief arrives on a phone through a
     webhook, and a later chat answers from the distilled memory with a cited
     path.

5. **M7 production readiness.**
   - TLS, PostgreSQL, Redis/arq, reverse proxy, backups and restore rehearsal,
     deployment guide and structured logs.
   - Exit criteria: a clean deployment can be backed up and restored, and meets
     the security bar for real accounts.

## 9. Running and verifying

```bash
# Full local stack: API 8000, index 8790, admin 5173
make start
make status
make stop
make restart

# Foreground single service
make api
make index-serve
make admin

# Android debug APK
make android

# Tests
cd apps/api && .venv/bin/python -m pytest -q
cd apps/android && ./gradlew testDebugUnitTest
```

The default `SERVICES` is `api index admin`; stop runs in reverse order. Logs
and PID files live under `.cache/logs/` and `.cache/run/`. The arq worker is
not part of the default stack because the checked-in configuration uses inline
jobs; see [operations](./operations.md).

Local secrets are in `apps/api/.env` (gitignored):

- `VOLCENGINE_ASR_API_KEY` — file and streaming Doubao ASR.
- `DEEPSEEK_API_KEY` — read by the DeepSeek supplier API; only needed when a feature is bound to it
  `openai_compatible`.
- `WINNIE_PUBLIC_URL` — build-time default server address for the APK.

Changing `.env` requires `make restart`; `uvicorn --reload` watches Python
files, not the environment file.

## 10. References

- [README](../README.md) — product overview, quick start, security notes.
- [Operations](./operations.md) — service inventory, ports, lifecycle and
  production shape.
- [Meeting-notes design](./meetings.md) — domain model, API, provider
  interfaces, delivery phases.
- [Backlog](./todo.md) — concrete, prioritized follow-ups.
- [Usage ledger](./usage.md) — what is recorded, token normalization, prices,
  provider balances and limits.
- [Decisions](./decisions.md) — provider, storage, Android and live-caption
  rationale.
- [Android client](../apps/android/README.md) — build, run, tests and
  limitations.
