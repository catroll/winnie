# Cross-lingual AI meeting notes — integration design

Status: backend scaffold implemented; real providers and the Android client are
pending. This document covers how the meeting-notes capability fits into
Winnie, how it reuses the existing backend, and how it is delivered in
increments.

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

This assumes the Step 0 structure from the code-structure review: `main.py` is a
composition root and the gateway is a feature module. If Step 0 has not been
done, add the new packages flat and move them in the same pass.

Target layout; some entries are planned and not built yet:

```text
apps/
├── api/                              # Python backend — one deployable
│   ├── src/winnie_api/
│   │   ├── main.py                   # create_app(): composition root
│   │   ├── settings.py               # YAML + env, extra="forbid"
│   │   ├── shared/
│   │   │   ├── db.py                 # async engine / session factory
│   │   │   ├── storage.py            # Storage interface + local/OSS
│   │   │   ├── logging.py            # structured logs, request/job IDs
│   │   │   └── llm.py                # internal model client (no HTTP loopback)
│   │   ├── gateway/                  # existing transparent proxy — untouched
│   │   ├── knowledge/                # documents, Git store, index
│   │   │   └── meeting_note.py       # minutes -> Markdown -> Git commit
│   │   ├── meetings/                 # NEW bounded context
│   │   │   ├── models.py             # SQLAlchemy models
│   │   │   ├── schemas.py            # API request/response models
│   │   │   ├── repository.py         # DB queries
│   │   │   ├── service.py            # use cases + state machine
│   │   │   ├── router.py             # /api/v1/meetings...
│   │   │   ├── pipeline.py           # LangGraph post-processing
│   │   │   └── prompts/              # versioned prompts
│   │   ├── integrations/             # external providers
│   │   │   ├── asr/{base,volcengine,aliyun,whisper}.py
│   │   │   ├── translate/{base,llm,cloud}.py
│   │   │   └── llm/{base,openai_compatible}.py
│   │   └── jobs/
│   │       ├── queue.py              # arq enqueue helpers
│   │       ├── tasks.py              # transcribe / translate / summarize
│   │       └── worker.py             # arq WorkerSettings entry
│   ├── alembic/                      # migrations
│   └── tests/
│       ├── fakes/                    # FakeASR, FakeTranslator, FakeLLM
│       └── meetings/
├── android/                          # NEW Kotlin app
│   └── app/src/main/java/<pkg>/
│       ├── ui/                       # Compose screens
│       ├── data/                     # API client, Room cache, upload queue
│       ├── audio/                    # AudioRecord + foreground service
│       └── realtime/                 # WebSocket client (phase 2)
└── web/                              # later
```

Dependency rules:

```
meetings/ ──► integrations/ ──► shared/
    │
    └──► knowledge/ (only at the end of the pipeline)
gateway/ ──► shared/          # gateway must not import meetings
jobs/ ──► meetings/
```

- `gateway/` stays transparent. No ASR, translation or RAG logic goes into
  `/{route}/v1/...`.
- `meetings/` must be testable without FastAPI, network or a database.
- `integrations/` hides vendor SDKs behind protocols.
- `knowledge/` is a sink, not a dependency of transcription.

## 4. Domain model

Use PostgreSQL. All tables carry `user_id` from day one, even while the app is
single-user; that is the seam for later isolation.

### 4.1 Tables

```text
meetings
  id                 uuid pk
  user_id            text not null
  title              text not null
  local_language     text not null default 'zh'
  source             text not null          -- 'file' | 'realtime'
  status             text not null          -- see state machine
  stage              text                   -- current pipeline stage
  audio_key          text                   -- storage key, not a public URL
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
- Realtime mode: ASR appends segments as they are finalized. Language detection
  and translation can run incrementally for the live view, but when the meeting
  ends the same normalization/translation/summarization functions run on the
  persisted segments. Do not fork the logic into a "live version" and a "final
  version".

### 5.2 Language detection and translation rules

- `local_language` is a per-meeting setting, default `zh`.
- Detect language **per segment**, not per meeting. A meeting can switch
  languages mid-sentence, and one global guess would mistranslate the rest.
- Prefer the ASR provider's detected language when its confidence is above the
  configured threshold. Otherwise call a `LanguageDetector`.
- If `detected_language == local_language`, set `translation_status = 'skipped'`
  and leave `translated_text` null. Always keep the original `text`.
- Translate only the differing segments, batched by meeting and language pair.
  Preserve `seq` so the UI can align original and translation line by line.
- For code-switching inside a single segment, translate the dominant language
  in the MVP. Word-level alignment is out of scope.
- Short utterances are unreliable for language ID. Use a minimum character or
  duration threshold before trusting a detection; otherwise inherit the
  previous segment's language.

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

## 6. API surface

All product endpoints live under `/api/v1`; the existing gateway contract under
`/{route}/v1/...` is untouched.

| Method | Path | Purpose |
|--------|------|---------|
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
| `WS` | `/api/v1/meetings/{id}/stream` | Realtime audio in, events out (phase 2) |

Auth: `Authorization: Bearer <token>` for the MVP, resolved to a `user_id`.
Keep the resolver behind a dependency so accounts/OAuth can replace it without
touching the meeting service. Android stores the token in
`EncryptedSharedPreferences`.

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
    async def translate(self, texts: list[str], *, source: str | None, target: str) -> list[str]: ...

class LanguageDetector(Protocol):
    def detect(self, text: str) -> LanguageGuess: ...

class Summarizer(Protocol):
    async def summarize(self, transcript: TranscriptView) -> MinutesDraft: ...
```

- **ASR**: start with one provider (Volcengine Doubao or Aliyun Tingwu) behind
  `ASRProvider`. Add the second only after measuring accuracy and latency on
  real recordings. Local `faster-whisper` is a separate implementation of the
  same protocol, selected by config.
- **Translation**: an LLM call behind `Translator` is usually good enough for
  meeting text and reuses the same model access. A dedicated translation API is
  an alternative implementation, not a rewrite.
- **Summarization**: `Summarizer` over an OpenAI-compatible model (DeepSeek or
  Qwen). Keep prompts in `meetings/prompts/` with an explicit version.
- **Config** selects the implementation per capability, e.g.
  `meetings.asr.provider: volcengine`. Credentials come from environment
  variables, never from YAML committed to Git.
- **Cost**: every provider call writes a `usage_records` row (audio seconds,
  tokens, characters). Provider prices live in config so the estimate can be
  recomputed. This is the basis of the per-meeting cost view.

## 8. Integration with the existing backend

### 8.1 Composition root

`create_app()` includes the meetings router only when the feature is enabled:

```python
app = create_app(settings)
if settings.meetings.enabled:
    app.include_router(meetings_router, prefix="/api/v1")
```

If `meetings.enabled` is false, the gateway must still start without Postgres,
Redis or OSS. This keeps the transparent proxy independently runnable.

### 8.2 Settings

Add explicit sections; reject unknown keys:

```yaml
meetings:
  enabled: false
  storage: local            # local | oss
  local_root: ../../data/meetings
  max_upload_mb: 500
  local_language: zh
  asr:
    provider: volcengine
    language_confidence_threshold: 0.7
  translate:
    provider: llm
  summarize:
    provider: llm
    model: deepseek-v4-flash
    prompt_version: v1
database:
  url: ""                   # env: WINNIE_DATABASE_URL
redis:
  url: ""                   # env: WINNIE_REDIS_URL
```

Secrets and connection strings come from environment variables. The existing
`config.py` currently ignores unknown keys and hard-codes "no environment
variables"; both should be fixed in Step 0 rather than carried into a larger
feature.

### 8.3 Gateway and LLM access

- Do not call the transparent gateway over loopback from the pipeline. That adds
  latency, makes the proxy a dependency of internal jobs, and muddles its logs.
- Put an internal `ModelClient` in `shared/llm.py` that talks to the provider
  directly (or through LiteLLM). Both the gateway and the AI jobs may use the
  same provider configuration, but through different call paths.
- Do not reuse `InteractionRecorder` for pipeline calls. It records transparent
  client conversations with `client_url`/`upstream_url`; internal AI calls need
  prompts, token usage and cost, which belong in `usage_records` plus structured
  logs.
- Reuse `shared/http.py` and the existing request-ID/logging conventions so a
  meeting job can be traced end to end.

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

The Android app is a client of `/api/v1`. It should not contain AI logic. The
FastAPI OpenAPI schema is the contract; generate the client or hand-write it,
but do not maintain a second set of request/response models by hand.

```text
apps/android/app/src/main/java/<pkg>/
├── ui/           Compose: record, meeting list, transcript, minutes
├── data/         HTTP client, Room cache, WorkManager upload
├── audio/        AudioRecord, foreground service, chunking
├── realtime/     WebSocket client (phase 2)
└── di/           dependency injection
```

MVP flow:

1. Record to a local file with a foreground service and a persistent
   notification.
2. Upload with `WorkManager` so a killed app can retry.
3. Poll meeting status; show progress per stage.
4. Show transcript with original/translation side by side, and a toggle.
5. Show minutes; export Markdown or TXT via the system share sheet.

Do not build realtime streaming in the first two weeks. The proposal's own
recommendation is file-first, and the Android foreground-service + WebSocket
work is where the schedule usually breaks.

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

- LangGraph pipeline: normalize → detect → translate → summarize → persist.
- Structured minutes with Pydantic validation and prompt versioning.
- Minutes API, regenerate, Markdown/TXT export, history list.
- Android: minutes screen, export/share, offline cache.
- Knowledge note writer (summary/decisions/actions only).

### Phase 3 — realtime

- WebSocket endpoint; streaming ASR; incremental translation.
- Android foreground-service streaming and reconnect.
- Final pass reuses the phase-1/2 pipeline unchanged.

### Phase 4 — scale and polish

- Speaker diarization, timestamps, hotword lists.
- Cross-meeting semantic search over knowledge notes.
- Local `faster-whisper` fallback.
- Multi-user isolation, quotas, cost dashboard.

## 11. Cost, privacy and operations

- **Cost**: record audio seconds and tokens per meeting; prices in config; show
  per-meeting and per-month estimates. The 1–1.5 CNY/hour estimate is a
  hypothesis until measured on the chosen providers.
- **Privacy**: meeting audio and transcripts are among the most sensitive data
  in the assistant. Keep OSS buckets private, use short-lived presigned URLs,
  offer local storage/Whisper, and implement deletion that purges audio,
  segments and derived notes. Do not commit transcripts to Git.
- **Retention**: make audio retention configurable; transcripts and minutes can
  outlive audio.
- **Idempotency**: unique job rows, conditional state transitions, and a
  per-stage dedupe key. A retried ASR call must not append a second copy of
  every segment.
- **Failure handling**: provider timeouts and rate limits are normal. Retry with
  backoff, cap attempts, and surface the failed stage in the UI.
- **Observability**: structured logs with `meeting_id`, `job_id`, `stage` and
  provider latency; usage records double as a cost ledger.

## 12. Decisions and open questions

Decisions taken in this design:

1. Meeting notes is a bounded context inside the Winnie backend, not a separate
   service and not a separate product.
2. One FastAPI backend for the MVP; Go was evaluated and dropped.
3. File-first MVP; realtime shares the same tables and pipeline.
4. Language detection is per segment; original text is always preserved.
5. The pipeline writes summary/decisions/action items into the knowledge base,
   not the raw transcript.
6. PostgreSQL for meeting data, Redis/arq for jobs, storage behind an interface.

Open questions still to settle:

- Auth: single-user token for the MVP, or accounts from day one?
- Storage: local disk for the MVP, or OSS immediately?
- ASR vendor: Volcengine or Aliyun? Decide by measuring accuracy on real
  bilingual recordings, not by feature list.
- Translation: LLM or a dedicated translation API? Measure latency and quality
  on the same samples.
- Minutes template: fixed, or user-editable per meeting type?
- Does the user want raw transcripts searchable, or only the derived note?

## 13. Implementation status

Implemented in the backend scaffold:

- `meetings.enabled` feature flag; the gateway still starts without a database
  when meetings are off.
- SQLAlchemy models for meetings, segments, minutes, jobs and usage records;
  SQLite for local development, PostgreSQL-ready URL.
- `/api/v1/meetings` CRUD, audio upload, transcribe, transcript, minutes,
  regenerate, export and delete.
- File-first pipeline: ASR -> per-segment language handling -> translation ->
  summarization -> Markdown note.
- Provider ports for ASR, translation and summarization, with deterministic
  fake implementations for tests and demos.
- Inline job queue by default, arq queue and worker entry point behind config.
- Tests using a fake upstream and a temporary SQLite database.

Deliberate deferrals and why:

- **LangGraph**: the pipeline is plain async Python. There are no branches or
  stateful retries yet; adding a graph now would add a dependency without
  removing code. Revisit when real provider retries, conditional re-runs or
  multi-step tools appear. The pipeline currently lives in
  `meetings/service.py`; `meetings/pipeline.py` is reserved for that change.
- **Alembic**: development uses `auto_create_tables`. Add migrations before the
  first production deployment and turn that flag off.
- **Real providers**: ASR (Volcengine/Aliyun), LLM translation and LLM
  summarization are ports without adapters. The fake providers make the HTTP
  and persistence path testable without cloud credentials.
- **Android and realtime**: not started. File-first remains the main path.
- **Knowledge Git write**: the note writer writes Markdown to a local
  directory; the Git commit/versioning layer is still the knowledge-base task.
