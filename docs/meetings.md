# Cross-lingual AI meeting notes — integration design

Status: the backend, accounts, provider adapters and the Android MVP are
implemented; file and streaming ASR are validated against the live Doubao
endpoints. A physical-device live-caption run, foreground-service recording and
a long bilingual meeting remain. See [plan.md](./plan.md) for milestone status
and completion evidence. This document covers how the meeting-notes capability
fits into Winnie, how it reuses the existing backend, and how it is delivered
in increments.

## 1. Where this fits

Meeting notes is a **capability of the personal assistant**, not a second
product. It belongs to the same product loop as the knowledge base:

```
record meeting -> transcribe -> detect language -> translate -> minutes
      -> write a knowledge note (summary, decisions, action items)
      -> later: ask the assistant about past meetings
```

The differentiator is the last step. A conventional meeting app stops at an
exported Markdown file. Winnie should write the result into the personal
knowledge base as a Markdown note with source metadata, so decisions and action
items become searchable alongside everything else the assistant knows.

Keep it as a **bounded context** with its own tables, service and router. It can
be extracted into a standalone service later if it outgrows Winnie, but it
should not start there.

## 2. Runtime topology

### 2.1 Decision: Python only

Go was evaluated and dropped. The backend stays Python, matching the existing
FastAPI codebase and the rest of the AI work.

| | A. One Python backend (chosen) | B. Go BFF + Python AI service |
|---|---|---|
| MVP delivery | One deployable, one auth path | Two services, two configs, two auth paths |
| Data ownership | Python owns meetings, segments and minutes | Go must proxy or duplicate writes; two writers is a drift risk |
| Streaming | FastAPI/Starlette WebSocket is enough for personal scale | Go wins at high concurrency |
| Ops | `api` + `worker` | `gateway` + `api` + `worker` |
| Time to first demo | Lower | Higher |

The file-first MVP has low concurrency, a single user, and no need for edge
rate limiting. A second language and service would double the surface area for
auth, deployment and configuration while the product is still being validated.
**Use one FastAPI backend plus one worker.**

### 2.2 What would have to change

Revisit the decision only if a concrete trigger appears:

- multi-tenant auth, quotas or rate limiting become real;
- concurrent realtime audio streams exceed what one Python process handles;
- the AI workload must be isolated from the public API for operational reasons.

If that happens, keep the boundary clean: the edge service owns TLS, auth, rate
limiting and WebSocket termination; Python keeps the meeting schema and the AI
pipeline. Until then, `apps/api` is the only backend.

## 3. Repository layout

`apps/api` is a thin host. The framework and capabilities are separate
packages, so a capability cannot import the host or another capability's
implementation.

```text
winnie/
├── apps/
│   ├── api/                          # Python host — one deployable
│   │   ├── src/winnie_api/
│   │   │   ├── main.py               # create_app(): registers capabilities
│   │   │   ├── runtime.py            # DB + auth + meeting service lifecycle
│   │   │   ├── settings.py           # YAML composition, path resolution
│   │   │   ├── jobs/
│   │   │   │   ├── tasks.py          # arq job functions
│   │   │   │   └── worker.py         # WorkerSettings entry point
│   │   │   └── cli/                  # host-only cleanup/reset commands
│   │   ├── migrations/               # Alembic, imports both model packages
│   │   └── tests/                    # host-level HTTP and integration tests
│   └── android/                      # Kotlin client (separate workstream)
└── packages/
    ├── winnie-core/                  # framework
    │   └── src/winnie_core/
    │       ├── capability.py         # Capability + WinnieFramework
    │       ├── config.py             # AuthConfig, JobsConfig, ModelRuntimeConfig
    │       ├── db.py, storage.py, types.py
    │       ├── auth/                 # models, service, router, CLI-free helpers
    │       ├── jobs/queue.py         # InlineJobQueue, ArqJobQueue
    │       └── llm/                  # internal OpenAI-compatible client
    ├── winnie-gateway/               # transparent LLM gateway capability
    ├── winnie-meetings/              # ASR/translation/minutes capability
    └── winnie-knowledge/             # Markdown note sink + neutral DTOs
```

Dependency rules:

```text
winnie-core       -> third-party packages only
winnie-knowledge  -> stdlib only
winnie-gateway    -> winnie-core (Capability)
winnie-meetings   -> winnie-core + winnie-knowledge
apps/api          -> all packages (composition root)
apps/android      -> HTTP API only
```

- No capability imports `winnie_api`; only the host imports capabilities.
- `winnie-meetings` exposes a `MeetingService`; it does not know FastAPI's
  process lifecycle or host settings.
- `winnie-knowledge` accepts `MeetingNote` / `NoteContent` DTOs, so it does not
  import meeting ORM models.
- `winnie-core` owns cross-cutting auth, DB, storage, queue and LLM primitives;
  it does not know about meetings or gateway routes.
- CLI commands live in `apps/api/src/winnie_api/cli/` because they compose the
  host runtime across capabilities.
- `apps/api` mounts capabilities through `WinnieFramework.register()`; adding a
  capability does not require editing a central route table.

## 4. Domain model

Use PostgreSQL in production and SQLite for local development. All tables carry
`user_id` from day one so account isolation is a seam rather than a rewrite.

### 4.1 Tables

```text
users
  id                 uuid pk
  username           text unique not null  -- normalized lowercase
  password_hash      text not null          -- Argon2id
  display_name       text
  is_active          boolean not null
  created_at         timestamptz
  updated_at         timestamptz

auth_sessions
  id                 uuid pk
  user_id            uuid fk -> users
  token_hash         text unique not null   -- SHA-256 of the bearer token
  expires_at         timestamptz
  created_at         timestamptz
  last_used_at       timestamptz

login_throttles
  key                text pk               -- normalized username
  attempts           integer not null
  window_started_at  timestamptz not null
  locked_until       timestamptz
  updated_at         timestamptz

meetings
  id                 uuid pk
  user_id            text not null
  title              text not null
  local_language     text not null default 'zh'
  source             text not null          -- 'file' | 'realtime'
  status             text not null          -- see state machine
  stage              text                   -- current pipeline stage
  audio_key          text                   -- storage key, not a public URL
  audio_deleted_at   timestamptz            -- set when retention purged audio
  audio_duration_ms  integer
  error              text
  created_at         timestamptz
  updated_at         timestamptz

segments
  id                 uuid pk
  meeting_id         uuid fk -> meetings
  seq                integer not null       -- stable order, never renumber
  start_ms           integer
  end_ms             integer
  speaker            text                   -- nullable; diarization later
  text               text not null          -- original
  detected_language  text
  language_confidence real
  translated_text    text                   -- null when already local language
  translation_status text                   -- 'pending' | 'done' | 'failed' | 'skipped'
  unique (meeting_id, seq)

minutes
  meeting_id         uuid pk fk -> meetings
  summary            text
  topics             jsonb
  decisions          jsonb
  action_items       jsonb
  highlights         jsonb
  model              text
  prompt_version     text
  created_at         timestamptz

jobs
  id                 uuid pk
  meeting_id         uuid fk
  kind               text                   -- 'transcribe' | 'translate' | 'summarize'
  status             text                   -- 'queued' | 'running' | 'done' | 'failed'
  attempts           integer
  last_error         text
  created_at, started_at, finished_at
  unique (meeting_id, kind)                 -- idempotency

usage_records
  id                 uuid pk
  meeting_id         uuid fk
  provider           text
  unit               text                   -- 'audio_second' | 'token' | 'character'
  quantity           numeric
  cost_micros        bigint                 -- integer currency avoids float drift
  created_at         timestamptz
```

### 4.2 State machine

```
created
  -> uploaded          audio stored
  -> queued            transcription job enqueued
  -> transcribing
  -> transcribed       segments persisted
  -> translating       only segments whose language differs
  -> summarizing
  -> ready             minutes persisted, knowledge note written
  -> failed            at any stage; `stage` and `error` say where
```

All transitions are conditional updates (`WHERE status = ...`) so a retried job
cannot move a meeting backwards. `jobs` has a unique `(meeting_id, kind)` row;
retries increment `attempts` on the same row rather than creating duplicates.

## 5. Processing pipeline

### 5.1 One pipeline, two inputs

This is the most important structural rule from the proposal: realtime and
file-based transcription must share the same data model and post-processing
pipeline.

```
file upload ──► ASR ─────────────┐
                                 ├──► segments table
realtime WS ──► streaming ASR ───┘
                                            │
                                            ▼
                    normalize -> detect languages -> translate non-local
                    -> persist -> summarize -> persist minutes
                    -> write knowledge note
```

- File mode: ASR returns a full segment list, then the pipeline runs once.
- Realtime mode (current): the WebSocket sends PCM to streaming ASR and returns
  provisional captions. The client uploads the same WAV after stop, so the
  file pipeline still produces the canonical transcript, translation and
  minutes. This keeps one accuracy path; the cost is a second ASR call per
  meeting. Persisting live segments and translating them incrementally remains
  a follow-up, not a second pipeline.
- Do not fork the normalization/translation/summarization logic into a "live
  version" and a "final version".

### 5.2 Language detection and translation rules

- `local_language` is a per-meeting setting, default `zh`.
- Detect language **per segment**, not per meeting. A meeting can switch
  languages mid-sentence, and one global guess would mistranslate the rest.
- Prefer the ASR provider's per-segment language when present. Otherwise use
  the script detector in `meetings/language.py`: Latin, CJK, kana and hangul
  are counted, and a script needs 40% of the letters to win. This covers
  Chinese/English meetings and gives Japanese/Korean a coarse label without a
  provider call. Replace it with a real `LanguageDetector` when accuracy on
  accented or code-switched speech matters.
- If `detected_language == local_language`, set `translation_status = 'skipped'`
  and leave `translated_text` null. Always keep the original `text`.
- Translate only the differing segments, batched by meeting and language pair.
  Preserve `seq` so the UI can align original and translation line by line.
- For code-switching inside a single segment, translate the dominant language
  in the MVP. Word-level alignment is out of scope.
- Short utterances are unreliable for language ID. Use a minimum character or
  duration threshold before trusting a detection; otherwise inherit the
  previous segment's language, falling back to `local_language` for the first
  segment.

### 5.3 Minutes schema

Ask the model for structured JSON and validate it with Pydantic. On validation
failure, retry once with the validation error appended to the prompt; then fail
the job rather than storing malformed minutes.

```json
{
  "summary": "string",
  "topics": [{"title": "string", "points": ["string"]}],
  "decisions": [{"text": "string", "context": "string"}],
  "action_items": [
    {
      "text": "string",
      "owner": "string|null",
      "due_date": "YYYY-MM-DD|null",
      "status": "open"
    }
  ],
  "highlights": [{"segment_seq": 12, "text": "string"}]
}
```

Store `model` and `prompt_version` with every minutes row. Regeneration must be
possible without re-transcribing, and must produce an auditable new row or a
version field.

Long transcripts are split by rendered characters into `summary_chunk_chars`
chunks. Each chunk returns partial minutes; one final merge call combines them,
and usage is summed across every call. Highlights in the merged result are
validated against the full transcript, so a chunk-local sequence cannot leak
into the stored minutes.

## 6. API surface

All product endpoints live under `/api/v1`; the existing gateway contract under
`/{route}/v1/...` is untouched.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/auth/register` | Create an account (first account, or any when registration is open) |
| `POST` | `/api/v1/auth/login` | Exchange username/password for a bearer token; `429` when locked |
| `POST` | `/api/v1/auth/logout` | Revoke the current session |
| `POST` | `/api/v1/auth/password` | Change password and revoke every session |
| `GET` | `/api/v1/auth/me` | Current account |
| `POST` | `/api/v1/meetings` | Create a meeting (`title`, `local_language`) |
| `POST` | `/api/v1/meetings/{id}/audio` | Upload audio (multipart; size/type limits) |
| `POST` | `/api/v1/meetings/{id}/transcribe` | Enqueue transcription; `202` with the current meeting status (poll `GET`) |
| `GET` | `/api/v1/meetings` | History, paginated |
| `GET` | `/api/v1/meetings/{id}` | Status, stage, duration, error |
| `GET` | `/api/v1/meetings/{id}/transcript` | Segments with original + translation |
| `POST` | `/api/v1/meetings/{id}/minutes/regenerate` | Re-run summarization |
| `GET` | `/api/v1/meetings/{id}/minutes` | Structured minutes |
| `GET` | `/api/v1/meetings/{id}/export?format=markdown\|txt` | Download |
| `DELETE` | `/api/v1/meetings/{id}` | Delete meeting and purge audio |
| `WS` | `/api/v1/meetings/{id}/stream` | Live PCM in, provisional caption events out; the file pipeline still produces the final transcript |

Auth: accounts are implemented with opaque bearer sessions. Passwords are
hashed with Argon2id, only a SHA-256 hash of the session token is stored, and
sessions expire after `auth.session_ttl_days`. The first account can always
register; later registrations are controlled by `auth.allow_registration`.
Changing a password requires the current password and revokes all sessions,
including the caller's. Failed logins are counted per normalized username; once
`auth.login_max_attempts` is reached within `auth.login_window_seconds`, login
returns `429` with `Retry-After` for `auth.login_lockout_seconds`. A successful
login clears the counter. The key is the username, not the client IP, so this
also protects a deployment behind a proxy; it does mean an attacker can
temporarily lock a known account. With no email service, a lost password is
reset from the host with `python -m winnie_api.cli.reset_password`; this
requires database access and revokes every session.
Meeting ownership uses the authenticated `user.id`, while `auth.enabled: false`
keeps the local single-user fallback. Android stores the token encrypted with
a key held in the Android Keystore (AES/GCM).

Job status: polling `GET /meetings/{id}` is enough for the MVP. Do not build a
WebSocket just to report progress; reserve WebSocket for realtime audio.

Upload: stream `UploadFile` to the `Storage` implementation; never load a long
recording into memory. Presigned OSS uploads are a phase-2 optimization; the MVP
can accept multipart with a size limit and store locally or in OSS.

## 7. External providers

Define protocols first, then one implementation each. This is what keeps the
ASR vendor, translation vendor and LLM swappable.

```python
class ASRProvider(Protocol):
    async def transcribe(self, audio: AudioRef, *, language_hint: str | None) -> ASRResult: ...

class Translator(Protocol):
    async def translate(
        self, texts: list[str], *, source: str | None, target: str
    ) -> TranslationResult: ...

class LanguageDetector(Protocol):
    def detect(self, text: str) -> LanguageGuess: ...

class Summarizer(Protocol):
    async def summarize(self, transcript: TranscriptView) -> MinutesDraft: ...
```

- **ASR**: Volcengine Doubao (big-model recording-file API) is the first real
  adapter behind `ASRProvider`. It submits a local file as base64 and polls the
  query endpoint; see `integrations/asr/volcengine.py`. Evaluate Aliyun and
  other vendors on the same bilingual recordings before adding a second
  adapter; the follow-up is tracked in [todo.md](./todo.md). Local
  `faster-whisper` is a separate implementation of the same protocol.
- **Streaming ASR**: `RealtimeASRProvider` is the live equivalent. The
  Volcengine SAUC 2.0 adapter in `integrations/asr/volcengine_stream.py` opens
  `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel`, sends 100 ms gzipped
  PCM frames and returns cumulative captions; the browser-style handshake uses
  the same `X-Api-Key` and `volc.seedasr.sauc.duration`. It does not write a
  `usage_records` row yet, so the cost view currently undercounts live audio.
- **Translation**: an LLM call behind `Translator` is usually good enough for
  meeting text and reuses the same internal model access. The
  `openai_compatible` adapter batches by character count, requests a JSON array
  in input order, and records token usage. A dedicated translation API remains
  an alternative implementation.
- **Summarization**: `Summarizer` over an OpenAI-compatible model (DeepSeek or
  Qwen). The `openai_compatible` adapter requests one JSON object, validates
  the shape and filters highlights that reference missing segments. Prompts
  live in code for now; versioned prompt files remain a later refactor.
- **Config** supplies only the ASR seed. `meetings.asr_provider` and
  `meetings.volcengine` seed the first ASR integration/binding; translation,
  minutes and the assistant are bound on the AI providers page. There is no
  `llm`, `translator` or `summarizer` key in `config.yaml`. Credentials come from
  `packages/winnie-llm/.env` (`VOLCENGINE_ASR_API_KEY`, and the variable derived
from each supplier id;
  integration), never from YAML committed to Git. The Doubao key is a single
  new-console API key sent as `X-Api-Key`; the old APP ID + Access Token pair is
  not supported.
- **Cost**: every file ASR, translation and summarization call writes a
  `usage_records` row (audio seconds, tokens, characters). The live-caption
  stream does not, so a realtime meeting is currently billed twice for ASR but
  only the file pass is recorded. Streaming usage is a follow-up. Provider
  prices live in config so the estimate can be recomputed.
- **LLM usage view**: translation and minutes calls also append to the console's
  usage ledger, which reports token classes, latency and cost per call and by
  feature (see [usage](./usage.md)). ASR stays in `usage_records` and is not on
  that page yet, so the two views answer different questions.

## 8. Integration with the existing backend

### 8.1 Composition root

`create_app()` builds one runtime when either auth or meetings is enabled:

```python
runtime = build_runtime(settings)   # None when both are disabled
if settings.auth.enabled:
    app.include_router(auth_router, prefix="/api/v1")
if settings.meetings.enabled:
    app.include_router(meetings_router, prefix="/api/v1")
```

The runtime owns a single async engine and session factory; auth and meetings
share it. With both features disabled, the gateway still starts without a
database, Redis or object storage. This keeps the transparent proxy
independently runnable.

### 8.2 Settings

```yaml
auth:
  enabled: true
  allow_registration: false   # first account still allowed
  session_ttl_days: 30
  login_max_attempts: 5
  login_window_seconds: 300
  login_lockout_seconds: 900

meetings:
  enabled: true
  storage: local
  local_root: ../../data/meetings
  notes_root: ../../data/kb/meetings
  max_upload_mb: 500
  audio_retention_days: 0        # 0 = keep forever; audio only, not minutes
  local_language: zh
  asr_provider: volcengine
  prompt_version: v1
  volcengine:
    base_url: https://openspeech.bytedance.com
    resource_id: volc.seedasr.auc
    stream_endpoint: wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
    stream_resource_id: volc.seedasr.sauc.duration
    model_name: bigmodel
    max_inline_mb: 100
    poll_interval_seconds: 2
    poll_timeout_seconds: 300


database:
  url: sqlite+aiosqlite:///../../data/winnie.db

jobs:
  backend: inline             # inline | arq
  redis_url: ""
```

With the shipped `ai.enabled: true`, the `meetings.asr_provider` and
`meetings.volcengine` values above are first-start seeds for `data/ai.yaml`;
afterwards the AI suppliers page selects the live ASR binding. Translation and
minutes start unbound and must be bound to a supplier model there. Secrets
(`VOLCENGINE_ASR_API_KEY` and the key derived from each supplier id) come from
`packages/winnie-llm/.env`, read by `winnie-llm` rather than the capability and
environment. Unknown YAML keys are rejected at startup. Production should use
PostgreSQL and run `uv run alembic upgrade head` with the same `WINNIE_CONFIG`
before setting `database.auto_create_tables: false`. A database created by
`auto_create_tables` has no `alembic_version`; back it up and `alembic stamp
head` instead of replaying the initial revision.

### 8.3 Gateway and LLM access

- Do not call the transparent gateway over loopback from the pipeline. That adds
  latency, makes the proxy a dependency of internal jobs, and muddles its logs.
- `integrations/llm/openai_compatible.py` is the internal model client. It talks
  to the provider directly and records token usage; the provider and model come
  from the `meetings.translate` / `meetings.summarize` binding in the AI
  registry, and the limits come from its runtime section.
- Do not reuse `InteractionRecorder` for pipeline calls. It records transparent
  client conversations with `client_url`/`upstream_url`; internal AI calls need
  prompts, token usage and cost, which belong in `usage_records` plus structured
  logs.
- Give the internal client its own timeout and error surface so a slow model
  fails the meeting stage cleanly instead of hanging the proxy client.

### 8.4 Knowledge-base sink

When minutes are ready, write a Markdown note:

```markdown
---
source: meeting
meeting_id: 5f0c...
date: 2026-10-20
local_language: zh
participants: []
projects: []
tags: [from-meeting]
---

## Summary
...

## Decisions
...

## Action items
- [ ] ...
```

Commit with a message such as `meeting: <title> (<id>)`. Do **not** put the raw
transcript in Git by default: it is large and sensitive. Link to the meeting
record instead. The note is what makes meeting output usable by the rest of the
assistant.

## 9. Android application

The Android app is a client of `/api/v1`. It contains no AI logic. The MVP is
implemented in `apps/android`; see
[apps/android/README.md](../apps/android/README.md) for build, run and tests.

```text
apps/android/app/src/main/java/dev/winnie/app/
├── ui/       Compose: login, meeting list, recorder, meeting detail
├── data/     HTTP client, DTOs, session and token storage
└── audio/    AudioRecord and incremental WAV writer
```

The DTOs are hand-written for the MVP because the surface is small. If it grows
past roughly twenty endpoints, generate the client from the OpenAPI schema
instead of maintaining a second set of models by hand.

Implemented MVP flow:

1. Record to a local WAV file at 16 kHz mono with `AudioRecord`.
2. Create the meeting, upload the file, request transcription.
3. Poll meeting status; the detail screen waits for `ready`.
4. Show the transcript with original and translation, then the minutes.

The recorder creates the meeting first, opens the live-caption WebSocket, and
shows provisional text while recording. On stop it uploads the WAV and runs the
file pipeline, which replaces the provisional text with the final transcript.
A failed stream is therefore a caption failure, not a lost recording.

Deferred and tracked in [todo.md](./todo.md): foreground-service recording and
a persistent notification, `WorkManager` upload retry, offline cache,
export/share, speaker labels, and reconnecting a dropped live stream. The
current live stream stops if the activity is destroyed.

## 10. Delivery plan

### Phase 0 — backend structure (before the feature)

Complete the Step 0 refactor from the code-structure review: `create_app()`,
`gateway/` package, tests with a fake upstream, config injection. Adding
meetings to the current `main.py` would make the god module worse.

### Phase 1 — file-first MVP (week 1)

- PostgreSQL schema + Alembic migrations; `Storage` with local disk.
- `POST /meetings`, audio upload, transcribe job, status polling.
- One ASR provider; per-segment language detection; translation of non-local
  segments via LLM.
- Transcript API with original/translation.
- Android: record, upload, list, transcript view.
- Tests: fakes for ASR/translation; state-machine and idempotency tests.

### Phase 2 — minutes and history (week 2)

- Async pipeline: normalize → detect → translate → summarize → persist.
  LangGraph is a later option, not a dependency of this phase.
- Structured minutes with Pydantic validation and prompt versioning.
- Minutes API, regenerate, Markdown/TXT export, history list.
- Android: minutes screen, export/share, offline cache.
- Knowledge note writer (summary/decisions/actions only).

### Phase 3 — realtime

- WebSocket endpoint and Doubao SAUC 2.0 streaming ASR adapter.
- Android live captions while recording; final pass reuses the phase-1/2
  pipeline unchanged.
- Remaining: foreground-service streaming, reconnect, and incremental
  translation of live segments.

### Phase 4 — scale and polish

- Speaker diarization, timestamps, hotword lists.
- Cross-meeting semantic search over knowledge notes.
- Local `faster-whisper` fallback.
- Quotas and a cost dashboard (account isolation is already implemented).

## 11. Cost, privacy and operations

- **Cost**: record audio seconds and tokens per meeting; prices in config; show
  per-meeting and per-month estimates. The 1–1.5 CNY/hour estimate is a
  hypothesis until measured on the chosen providers.
- **Privacy**: meeting audio and transcripts are among the most sensitive data
  in the assistant. Keep OSS buckets private, use short-lived presigned URLs,
  offer local storage/Whisper, and implement deletion that purges audio,
  segments and derived notes. Do not commit transcripts to Git.
- **Retention**: `audio_retention_days` purges only audio for terminal meetings;
  transcripts and minutes outlive it. Delete meeting already purges audio,
  segments, minutes, usage and the note immediately.
- **Sessions**: expired bearer sessions are rejected on use and removed by the
  arq daily cron or `python -m winnie_api.cli.cleanup_sessions` when no worker runs.
- **Idempotency**: unique job rows, conditional state transitions, and a
  per-stage dedupe key. A retried ASR call must not append a second copy of
  every segment.
- **Failure handling**: provider timeouts and rate limits are normal. The LLM
  client retries transport errors and retryable HTTP statuses with backoff; the
  ASR adapter still fails the stage immediately. Surface the failed stage in the
  UI and cap future ASR attempts.
- **Observability**: structured logs with `meeting_id`, `job_id`, `stage` and
  provider latency; usage records double as a cost ledger.

## 12. Decisions and open questions

Decisions taken in this design:

1. Meeting notes is a bounded context inside the Winnie backend, not a separate
   service and not a separate product.
2. One FastAPI backend for the MVP; Go was evaluated and dropped.
3. File-first MVP; realtime shares the same tables and pipeline.
4. Language detection is per segment; original text is always preserved.
5. Only minutes and notes enter the knowledge base. The raw transcript stays in
   the meeting database and is not indexed, which keeps the note corpus small
   and avoids copying sensitive verbatim text into another store.
6. Accounts from day one: Argon2id passwords, opaque bearer sessions, and
   per-user meeting ownership. The first account bootstraps the deployment;
   registration can then be closed.
7. Local disk for meeting audio and notes in this phase. The `Storage` port
   keeps object storage as a later adapter, not a rewrite.
8. Volcengine Doubao big-model recording-file ASR is the first real provider.
   Aliyun and local Whisper are evaluated later against the same bilingual
   recordings; this is tracked in [todo.md](./todo.md).
9. SQLite and the inline queue are the development defaults. PostgreSQL and
   arq/Redis are deployment options behind existing interfaces.
10. Translation and minutes use one internal OpenAI-compatible client, selected
    separately from the gateway routes. The first implementation targets
    DeepSeek; the same adapter works with any compatible endpoint.

Open questions still to settle:

- Translation quality and cost: compare the generic LLM adapter with a
  dedicated translation API on the same bilingual samples. The adapter exists;
  the measurement does not.
- Minutes template: fixed, or user-editable per meeting type?
- Which ASR vendor wins on bilingual accuracy, language-switch handling,
  latency and cost? Decide by measurement, not by feature list.

## 13. Implementation status

Implemented:

- Account system: registration, login, logout, password change,
  `GET /auth/me`, Argon2id password hashes, hashed opaque session tokens,
  failed-login lockout, first-user bootstrap and optional registration closure.
  Meetings are isolated by authenticated user.
- `auth.enabled` and `meetings.enabled` flags; the gateway still starts without
  a database when both are off.
- SQLAlchemy models for users, sessions, meetings, segments, minutes, jobs and
  usage records; SQLite for local development, PostgreSQL-ready URL.
- Alembic initial migration and `alembic check`; tests cover upgrade, downgrade
  and application startup with `auto_create_tables: false`.
- `/api/v1/meetings` CRUD, audio upload, transcribe, transcript, minutes,
  regenerate, export and delete.
- Audio retention by age (`audio_retention_days`, 0 keeps forever) with an arq
  daily cron and a standalone CLI; transcripts and minutes are kept.
- File-first pipeline: ASR -> per-segment language handling -> translation ->
  summarization -> Markdown note. Missing ASR labels are filled by a script
  detector (zh/en/ja/ko), with short segments inheriting their neighbour.
- Volcengine Doubao big-model recording-file ASR adapter with base64 inline
  upload, polling, usage recording and mocked HTTP tests. The credential is the
  new-console API key from `VOLCENGINE_ASR_API_KEY`, sent as `X-Api-Key`.
- OpenAI-compatible LLM adapters for batched translation and structured
  minutes, selected with the `meetings.translate` / `meetings.summarize` binding
  on the AI providers page; the key comes from the bound integration's
  `winnie_llm.asr`. Transient transport/HTTP failures are retried with backoff and
  `Retry-After` support; long transcripts use chunked map-reduce summarization.
- Provider ports for ASR, translation and summarization; deterministic fakes for
  tests and demos.
- Inline job queue by default; the arq queue and `WorkerSettings` entry point
  are verified end-to-end against Redis 8 and process a meeting to `ready`.
- `/healthz` liveness and `/readyz` readiness (database, plus Redis when arq is
  selected).
- Expired-session cleanup through an arq daily cron and
  `python -m winnie_api.cli.cleanup_sessions` for deployments without a worker.
- Tests using a fake upstream, temporary SQLite databases, account isolation, a
  mocked Volcengine endpoint and mocked LLM responses.

Deliberate deferrals and why:

- **LangGraph**: the pipeline is plain async Python. There are no branches or
  stateful retries yet; adding a graph now would add a dependency without
  removing code. Revisit when real provider retries, conditional re-runs or
  multi-step tools appear. The pipeline currently lives in
  `meetings/service.py`; `meetings/pipeline.py` is reserved for that change.
- **Migration rollout**: the initial schema plus both follow-up revisions were
  rehearsed on PostgreSQL 16 with seeded users, meetings, segments, minutes and
  usage rows; `alembic upgrade head` preserved them and the app completed a
  file-first meeting with `auto_create_tables: false`. No production deployment
  has run it yet, so take a backup and rehearse restore before the first one.
- **Live LLM validation**: the adapters passed a real DeepSeek smoke test for
  translation order, structured minutes, token usage and a provider 400 error.
  Prompt quality, cost and long-transcript behavior still need real bilingual
  recordings; the chunked map-reduce merge quality is not yet measured.
- **Other ASR vendors**: Aliyun, local Whisper and a bilingual accuracy
  benchmark are deferred until the Doubao path has run on real recordings.
- **Android and realtime**: the backend streaming path is validated against
  the live Doubao endpoint. The Android MVP now records with live captions and
  still runs the file-first final pass; a physical-device run with a live ASR
  key is pending. Foreground-service recording and reconnect are still open.
- **Knowledge Git write**: the note writer writes Markdown to a local
  directory; the Git commit/versioning layer is still the knowledge-base task.
