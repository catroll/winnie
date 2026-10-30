# Winnie API

FastAPI backend for Winnie. It ships the transparent LLM gateway, local
accounts, and the file-first meeting-notes backend; knowledge APIs are added
incrementally.

- Application entry and composition root: `winnie_api.main:create_app`
- Gateway feature: `src/winnie_api/gateway/`
- Auth feature: `src/winnie_api/auth/`
- Meetings feature: `src/winnie_api/meetings/`
- Shared runtime and database: `src/winnie_api/runtime.py`, `shared/db.py`
- Settings: `config.yaml`; local overlay `config.local.yaml` (gitignored)
- Tests: `tests/`, using a fake upstream instead of real vendor keys

## Run

```bash
# Edit config.yaml as needed.
uv sync
uv run uvicorn winnie_api.main:app --reload --host 127.0.0.1 --port 8000
curl -s http://127.0.0.1:8000/routes
```

With the default `config.yaml`, accounts and meeting notes are enabled and the
database is created on startup for development. Create the first account
(allowed even with `auth.allow_registration: false`):

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"correct horse battery"}'
```

The response contains a bearer token. Send it as
`Authorization: Bearer <token>` to `/api/v1/meetings` and `/api/v1/auth/me`.
Later registrations are rejected unless `auth.allow_registration: true`.
Failed logins are counted per username; after `auth.login_max_attempts` within
`auth.login_window_seconds`, login returns `429` with `Retry-After` until the
`auth.login_lockout_seconds` window passes. A successful login clears the
counter.

Change the password with the current password; this revokes every session, so
log in again afterwards:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/password \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"current_password":"...","new_password":"..."}'
```

If the password is lost and no email service is configured, reset it from the
host. This bypasses the current password and revokes every session, so it is
equivalent to having database access. `--generate` prints a random password
once; without it the command prompts twice.

```bash
uv run python -m winnie_api.auth.reset_password --generate alice
```

Expired sessions are rejected when used. Delete them periodically with the arq
worker (daily at 03:17 UTC) or, without a worker, a cron entry:

```bash
uv run python -m winnie_api.auth.cleanup
```

## Database migrations

`auto_create_tables` is a development convenience. For a real deployment, run
Alembic against the same config file and disable automatic table creation:

```bash
cd apps/api
WINNIE_CONFIG=/path/to/prod.yaml uv run alembic upgrade head
uv run alembic check
```

Set `database.auto_create_tables: false` in that config before starting the API
or worker. `WINNIE_CONFIG` is read by the application and by `migrations/env.py`,
so both target the same URL. Use `alembic downgrade base` only for a disposable
database.

A database that was created by `auto_create_tables` has the tables but no
`alembic_version`. Back it up, compare the schema with the models, then stamp
the initial revision instead of replaying it:

```bash
uv run alembic stamp head
```

Do not run `upgrade head` against a populated, unversioned database; it will
try to create tables that already exist.

## Test

```bash
uv run pytest
```

## Audio retention

`meetings.audio_retention_days: 0` keeps audio forever. A positive value purges
audio for terminal (`ready` or `failed`) meetings older than the window while
keeping the transcript and minutes. Run it from cron when no arq worker is
deployed:

```bash
uv run python -m winnie_api.meetings.cleanup_audio
```

The arq worker schedules the same purge daily at 03:47 UTC.
`DELETE /api/v1/meetings/{id}` still deletes audio and derived data immediately.

## Meeting API

The meeting backend is mounted under `/api/v1/meetings` when
`meetings.enabled: true`. It uses local SQLite and local disk by default, so it
runs without cloud storage. With `meetings.asr_provider: volcengine`, set:

```bash
export VOLCENGINE_ASR_API_KEY=...
```

This is the API key from the current Volcengine console, sent as `X-Api-Key`.
The legacy APP ID + Access Token pair is no longer accepted.

To try the full pipeline without cloud credentials, set
`meetings.asr_provider: fake` and keep `meetings.translator` /
`meetings.summarizer` as `fake` in `config.local.yaml`. The fake
ASR/translation/summarization providers are deterministic and are for local
demos and tests only.

For real translation and structured minutes, set both providers to
`openai_compatible`, configure the `llm` block in `config.yaml`, and export the
key named by `llm.api_key_env` (default `WINNIE_LLM_API_KEY`):

```yaml
meetings:
  translator: openai_compatible
  summarizer: openai_compatible
llm:
  base_url: https://api.deepseek.com/v1
  api_key_env: WINNIE_LLM_API_KEY
  model: deepseek-chat
  max_retries: 2
  retry_backoff_seconds: 1
  summary_chunk_chars: 24000
```

The internal LLM client calls the provider directly and does not route through
the transparent gateway. It retries transient transport errors and retryable
HTTP statuses with exponential backoff; 4xx responses fail fast. Transcripts
longer than `summary_chunk_chars` are summarized in chunks and merged in one
final call. Only the minutes note is written to the knowledge directory; the
raw transcript stays in the meeting database. See [docs/meetings.md](../../docs/meetings.md) for the
full design and [docs/todo.md](../../docs/todo.md) for provider validation and
production follow-ups.

```bash
# create, upload, process (TOKEN is the access_token from registration/login)
curl -s -X POST http://127.0.0.1:8000/api/v1/meetings \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"title":"Weekly sync"}'
curl -s -X POST http://127.0.0.1:8000/api/v1/meetings/<id>/audio \
  -H "Authorization: Bearer $TOKEN" \
  -F 'file=@recording.m4a'
curl -s -X POST http://127.0.0.1:8000/api/v1/meetings/<id>/transcribe \
  -H "Authorization: Bearer $TOKEN"

# read
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/v1/meetings/<id>/transcript
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/v1/meetings/<id>/minutes
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://127.0.0.1:8000/api/v1/meetings/<id>/export?format=markdown'
```

## Smoke test

The same `DEEPSEEK_API_KEY` exercises both protocol routes:

| Route | Protocol | Path |
|-------|----------|------|
| `deepseek-openai` | openai | `/deepseek-openai/v1/chat/completions` |
| `deepseek-anthropic` | anthropic | `/deepseek-anthropic/v1/messages` |

```bash
# scripts/.env contains DEEPSEEK_API_KEY
uv run python scripts/smoke_test.py
uv run python scripts/smoke_test.py --only openai
uv run python scripts/smoke_test.py --only anthropic
```
