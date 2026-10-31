# Winnie API

FastAPI backend for Winnie. It ships the transparent LLM gateway, local
accounts, and the file-first meeting-notes backend; knowledge APIs are added
incrementally.

- Host and composition root: `src/winnie_api/main.py`
- Framework package: `packages/winnie-core` (auth, DB, storage, jobs, LLM)
- Gateway package: `packages/winnie-gateway`
- Meetings package: `packages/winnie-meetings`
- Assistant package: `packages/winnie-assistant` (chat, tools, memory, tasks)
- Knowledge sink package: `packages/winnie-knowledge`
- Host settings: `config.yaml`; local overlay `config.local.yaml` (gitignored)
- Admin-managed AI registry: `data/ai.yaml` (gitignored), served by
  `src/winnie_api/ai/` and edited through the console
- Tests: `tests/`, using a fake upstream instead of real vendor keys

## Run

From the repository root, `make api` starts the same server, binds `[::]` for
IPv6, and sources an optional `apps/api/.env`; `make help` lists the overridable
variables. Use `API_HOST=0.0.0.0` for an IPv4-only network. `make stop` stops
the process listening on `API_PORT`, and `make restart` stops then starts it
again. Restart after editing `apps/api/.env`: `uvicorn --reload` watches Python
files, not the environment file.

```bash
# Edit config.yaml as needed.
uv sync
uv run uvicorn winnie_api.main:app --reload --host :: --port 8000
curl -s 'http://[::1]:8000/routes'
```

With the default `config.yaml`, accounts and meeting notes are enabled and the
database is created on startup for development. Create the first account
(allowed even with `auth.allow_registration: false`):

```bash
curl -s -X POST http://[::1]:8000/api/v1/auth/register \
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
curl -s -X POST http://[::1]:8000/api/v1/auth/password \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"current_password":"...","new_password":"..."}'
```

If the password is lost and no email service is configured, reset it from the
host. This bypasses the current password and revokes every session, so it is
equivalent to having database access. `--generate` prints a random password
once; without it the command prompts twice.

```bash
uv run python -m winnie_api.cli.reset_password --generate alice
```

Expired sessions are rejected when used. Delete them periodically with the arq
worker (daily at 03:17 UTC) or, without a worker, a cron entry:

```bash
uv run python -m winnie_api.cli.cleanup_sessions
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

## Background jobs

The default `jobs.backend: inline` runs the meeting pipeline inside the request,
which is convenient for a local demo but ties up the API. For a worker, set:

```yaml
jobs:
  backend: arq
  redis_url: redis://127.0.0.1:6379/0
```

Run the worker with the same config and storage roots as the API:

```bash
uv run arq winnie_api.jobs.worker.WorkerSettings
```

The API only enqueues `process_meeting_job`; the worker performs ASR,
translation, minutes and the note write. Expired-session and audio-retention
cron jobs also live in that worker. The arq/Redis path is verified end-to-end,
but inline remains the default until a deployment always runs a worker.

## Health checks

- `GET /healthz` is process liveness and does no I/O.
- `GET /readyz` runs `SELECT 1` against the configured database and, when
  `jobs.backend: arq`, a Redis `PING` with a two-second limit. It returns `503`
  with `database unavailable` or `queue unavailable` on failure.

## Audio retention

`meetings.audio_retention_days: 0` keeps audio forever. A positive value purges
audio for terminal (`ready` or `failed`) meetings older than the window while
keeping the transcript and minutes. Run it from cron when no arq worker is
deployed:

```bash
uv run python -m winnie_api.cli.cleanup_audio
```

The arq worker schedules the same purge daily at 03:47 UTC.
`DELETE /api/v1/meetings/{id}` still deletes audio and derived data immediately.

## AI providers

The host owns a small AI registry at `/api/v1/ai`. It lists the vendor
integrations (OpenAI-compatible, Anthropic, Volcengine ASR and the deterministic
fake) and which integration each AI-using feature calls:

| Feature | Used by |
|---|---|
| `assistant.chat` | Assistant replies, tool calls and memory distillation |
| `meetings.transcribe` | File ASR and live captions |
| `meetings.translate` | Transcript translation |
| `meetings.summarize` | Structured minutes |

`config.yaml` seeds the registry on first start from `routes`,
`meetings.asr_provider` and `meetings.volcengine`, then writes `data/ai.yaml`.
There is no `llm` block, `assistant.model`, `meetings.translator` or
`meetings.summarizer`: the registry is the only model choice. Routes seed as
`gateway: true` with an empty `api_key_env`; the three model features start bound
to `fake`, and ASR follows the legacy `asr_provider` value. After that file
exists the console is authoritative. An unbound feature has no provider and
fails with a named error; there is no YAML fallback. `ai.enabled: false` (the
programmatic `Settings()` default) leaves model features on fake and ASR on its
YAML default.

`gateway` controls exposure, not the kind: an OpenAI-compatible integration can
be a public `/{name}/v1/...` route, an internal-only provider, or both. A
gateway-only provider has no `api_key_env`; binding it to an internal feature is
rejected. A `version: 1` file (where a route doubled as the internal LLM) is
migrated on first read: the route key/model are cleared, a separate
`internal-llm` is created and the affected bindings are repointed.

Shared model limits (timeout, token cap, temperature, retries, chunk sizes) live
in the registry's `runtime` section and are edited from the console. They apply
to the next call and have no `config.yaml` equivalent.

```yaml
ai:
  enabled: true
  # Relative to apps/api/, like every other root.
  path: ../../data/ai.yaml
```

Endpoints (bearer session required):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/ai/config` | Integrations, feature bindings, runtime, kind metadata |
| `POST` | `/api/v1/ai/integrations` | Create an integration |
| `PUT` | `/api/v1/ai/integrations/{id}` | Update one |
| `DELETE` | `/api/v1/ai/integrations/{id}` | Delete it and unbind its features |
| `PUT` | `/api/v1/ai/features/{feature}` | Bind a feature (`integration_id: null` unbinds) |
| `PUT` | `/api/v1/ai/runtime` | Update the shared model limits |

```bash
curl -s -X POST http://[::1]:8000/api/v1/ai/integrations \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"deepseek","kind":"openai","base_url":"https://api.deepseek.com",
       "api_key_env":"DEEPSEEK_API_KEY","default_model":"deepseek-chat",
       "gateway":false}'
```

Set `"gateway": true` to expose an OpenAI/Anthropic provider at
`/{name}/v1/...`; the client supplies its own vendor key on those paths. An
internal-only provider uses `api_key_env` for the assistant and meetings. The
registry stores the variable name, never the key. A provider change applies on
the next model call, and the arq worker re-reads the file by modification time.
The console page is documented in
[docs/ai-providers.md](../../docs/ai-providers.md).

## LLM usage

Every internal model call and every call through the gateway appends one JSON
line to a local ledger. The console's **LLM usage** page reads it for token
classes, latency, estimated cost and provider balances. The ledger is switched
by `usage.enabled`; prices are configuration, not stored per call, so a
corrected price also corrects history.

```yaml
usage:
  enabled: true
  # Relative to apps/api/, like every other root.
  root: ../../data/usage
  currency: USD
  # Per 1M tokens. Keys match provider/model, then model, then "*".
  prices:
    deepseek-flash:
      input: 0.30
      output: 1.20
      cached_input: 0.006
  balance_ttl_seconds: 300
  balance_timeout_seconds: 8
```

No prices ship with the repository: vendor prices change, and peak/off-peak
rates cannot be expressed in one row. An unlisted model is counted as unpriced
instead of estimated at zero. A provider-reported cost (`usage.cost`, for
example on OpenRouter) takes precedence.

Balances are queried only for integrations whose `api_key_env` is set, and only
when the vendor exposes an endpoint: DeepSeek, OpenRouter, Kimi/Moonshot and
SiliconFlow. Gateway clients pass their own key, so proxied traffic is recorded
but not covered by a server-side balance.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/usage/overview?days=7` | Totals and by-day, by-capability and by-model breakdowns |
| `GET` | `/api/v1/usage/records?days=7` | Recent calls with per-call cost; filters and pagination |
| `GET` | `/api/v1/usage/balances` | Cached provider balances |
| `POST` | `/api/v1/usage/balances/refresh` | Force a fresh query |

The ledger is append-only and has no automatic retention. Meeting ASR usage
stays in the meeting database's `usage_records` table and is not part of this
page yet. Details, token normalization and limits are in
[docs/usage.md](../../docs/usage.md).

## Meeting API

The meeting backend is mounted under `/api/v1/meetings` when
`meetings.enabled: true`. It uses local SQLite and local disk by default, so it
runs without cloud storage. With the default `ai.enabled: true`, the first start uses
`meetings.asr_provider` and `meetings.volcengine` to seed the ASR integration and
binding. It does not seed a model: translation and minutes start on `fake`.
After that first start, choose providers and bindings on the AI providers page;
there is no YAML fallback for a model feature.

For Doubao ASR, export the key named by the integration (default
`VOLCENGINE_ASR_API_KEY`):

```bash
export VOLCENGINE_ASR_API_KEY=...
```

This is the API key from the current Volcengine console, sent as `X-Api-Key`.
The legacy APP ID + Access Token pair is no longer accepted.

To try the full pipeline without cloud credentials, bind transcription,
translation and minutes to the `fake` integration. The fake
ASR/translation/summarization providers are deterministic and are for local
demos and tests only.

For real translation and structured minutes, add an OpenAI-compatible
integration with a base URL, an `api_key_env` and a model, export that variable,
and bind both `meetings.translate` and `meetings.summarize` to it. Timeouts,
retries and chunk sizes come from the registry's **Model runtime** card, not
from `config.yaml`.

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
curl -s -X POST http://[::1]:8000/api/v1/meetings \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"title":"Weekly sync"}'
curl -s -X POST http://[::1]:8000/api/v1/meetings/<id>/audio \
  -H "Authorization: Bearer $TOKEN" \
  -F 'file=@recording.m4a'
curl -s -X POST http://[::1]:8000/api/v1/meetings/<id>/transcribe \
  -H "Authorization: Bearer $TOKEN"

# read
curl -s -H "Authorization: Bearer $TOKEN" \
  http://[::1]:8000/api/v1/meetings/<id>/transcript
curl -s -H "Authorization: Bearer $TOKEN" \
  http://[::1]:8000/api/v1/meetings/<id>/minutes
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://[::1]:8000/api/v1/meetings/<id>/export?format=markdown'
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

## Document index proxy

The local text index runs as its own daemon (`packages/winnie-index`). The API
proxies its management endpoints under `/api/v1/index/*` so the console keeps one
origin and one session:

| Route | Purpose |
|---|---|
| `GET /api/v1/index/status` | Coverage, roots, failures, last sweep |
| `GET /api/v1/index/config` | Editable configuration |
| `PUT /api/v1/index/config` | Validate and save; the daemon reloads without a restart |
| `POST /api/v1/index/search` | Chunk-level results for the console's search test |
| `POST /api/v1/index/build` | Dry run or real sweep |
| `POST /api/v1/index/skill` | Install or check the Agent Skill |

Configure it under `index:` in `config.yaml` (`enabled`, `base_url`,
`timeout_seconds`). A daemon that is down answers `503` with the address it
tried, which is the single most useful error here.

## Assistant

The assistant capability is enabled by `assistant.enabled` in `config.yaml` and
mounts `/api/v1/assistant`. Conversations, memory notes, tasks and run history
live under `assistant.root` (`~/Documents/Mine/Winnie` by default), which is
inside the local index root, so the index service finds them on its next sweep.
The files survive a restart and can be edited by hand.

```bash
# Login first when accounts are enabled; skip the header otherwise.
TOKEN=$(curl -s -X POST http://[::1]:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"..."}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

curl -s -X POST http://[::1]:8000/api/v1/assistant/chat \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"上海今天要带伞吗？"}'
```

Open `http://[::1]:8000/api/v1/assistant/ui` for the chat page. The model comes
only from the `assistant.chat` binding; a fresh registry binds it to `fake`, which
answers deterministically until a real provider is added and bound on the AI
providers page. Web search uses Bing RSS by default and needs no key.

Local recall goes through `winnie-index` (`make index-serve`, listening on
`127.0.0.1:8790`). With the daemon down, chat still works and the local-document
tools report why they failed. `GET /api/v1/assistant/documents/search?q=` exposes
the same search for debugging.

Scheduled tasks are created by asking the assistant; there is no file to edit.
The JSON at `<root>/tasks/tasks.json`, the schedule forms and the memory
pipeline are documented in [docs/assistant.md](../../docs/assistant.md).
