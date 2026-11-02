# Winnie API

FastAPI backend for Winnie. It ships the model gateway, local
accounts, and the file-first meeting-notes backend; knowledge APIs are added
incrementally.

- Host and composition root: `src/winnie_api/main.py`
- Framework package: `packages/winnie-core` (auth, DB, storage, jobs)
- Model access: `packages/winnie-llm` (chat, ASR, supplier registry and the
  only place vendor keys are read)
- Gateway package: `packages/winnie-gateway`
- Meetings package: `packages/winnie-meetings`
- Assistant package: `packages/winnie-assistant` (conversations, memory, tasks)
- Chat engine: `packages/winnie-chat` (model loop, skills, prompt)
- Local execution: `packages/winnie-local` (files, commands, review, audit)
- Knowledge sink package: `packages/winnie-knowledge`
- News pipeline package: `packages/winnie-news` (mounted as `/api/v1/news`;
  design and operations in [docs/news.md](../../docs/news.md))
- Phone data package: `packages/winnie-phone` (mounted as `/api/v1/phone`;
  design, contract and privacy notes in [docs/phone.md](../../docs/phone.md))
- Push package: `packages/winnie-push` (mounted as `/api/v1/push`; protocol and
  transport choice in [docs/push.md](../../docs/push.md))
- Phone geocoding: the server resolves a location fix to 省/市/区/具体位置 when
  `phone.geocode` is enabled, and `POST /api/v1/phone/locations/geocode` backfills
  stored fixes; see [docs/phone.md](../../docs/phone.md)
- Schedule package: `packages/winnie-schedule` (the owner's weekly Markdown
  file tree, mounted as `/api/v1/schedule` by the host, not a capability)
- Host settings: `config.yaml`; local overlay `config.local.yaml` (gitignored)
- Supplier registry: `data/ai.yaml` (gitignored), owned by
  `packages/winnie-llm` and exposed by `src/winnie_api/ai/` for the console
- Gateway keys: `data/gateway-keys.yaml` (gitignored, mode `0600`, digest only);
  created and revoked through `src/winnie_api/gateway/`
- Tests: `tests/`, using a fake upstream instead of real vendor keys

## Run

From the repository root, `make api` starts the same server, binds `[::]` for
IPv6, and sources an optional `apps/api/.env`; `make help` lists the overridable
variables. Use `API_HOST=0.0.0.0` for an IPv4-only network. `make start`
starts the API, index and admin console; `make stop` and `make restart` manage
that whole stack. Scope a command to the API with `SERVICES=api`, for example
`make stop SERVICES=api`. Restart after editing `apps/api/.env`: `uvicorn
--reload` watches Python files, not the environment file. A supplier key saved
on the console needs no restart.

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

The host owns a small AI registry at `/api/v1/ai`. A **supplier** (DeepSeek,
OpenAI, Qwen, Anthropic, Volcengine) owns one internal API, a **model
catalog** and any public gateway routes. Every AI-using feature binds to a
**model**; the supplier is resolved from that model. Features never select an
endpoint:

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
`gateway: true` endpoints and are grouped under their supplier
(`deepseek-openai` → DeepSeek). No fake supplier ships: every model
feature starts unbound until the owner adds a supplier API, syncs its models and
chooses one. ASR follows the legacy `asr_provider` value. After that file exists
the console is authoritative. An unbound feature has no model and fails with a
named error; there is no YAML fallback. `ai.enabled: false` (the programmatic
`Settings()` default) leaves model features unconfigured and ASR on its YAML
default.

`gateway` controls exposure, not the kind: an OpenAI-compatible record can be a
public `/{name}/v1/...` route, the supplier's internal API, or both. A
gateway-only record cannot serve a feature; only the internal API can. A
`version: 1` file is migrated in place: the old combined route is split into a
gateway route and an internal API, and bindings are repointed. A `version: 2`
file is grouped under suppliers and its models are collected. A `version: 3`
file is converted to `{vendor, model}` bindings; a `version: 4` file drops the
seeded fake supplier and clears its bindings; a `version: 5` file loses the
stored key variable and becomes `version: 6`; a `version: 6` file imports each
supplier key from `packages/winnie-llm/.env` and becomes `version: 7`. Removing a
model that a binding or endpoint default still uses is rejected.

The registry and every vendor client belong to `packages/winnie-llm`. The
console API here is only the HTTP surface. Each supplier's API key is part of
its registry record, stored with its models and written from this console; the
same key is mirrored into `packages/winnie-llm/.env` as a backup.

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
| `GET` | `/api/v1/ai/config` | Suppliers, models, endpoints, bindings, runtime, kind metadata |
| `POST` | `/api/v1/ai/vendors` | Create a supplier |
| `PUT` | `/api/v1/ai/vendors/{id}` | Update a supplier label/models |
| `PUT` | `/api/v1/ai/vendors/{id}/key` | Store `{api_key}` (empty clears); mirrors the env backup |
| `POST` | `/api/v1/ai/vendors/{id}/models/sync` | Fetch the supplier's `/models` list |
| `POST` | `/api/v1/ai/integrations` | Create the supplier API or a gateway route |
| `PUT` | `/api/v1/ai/integrations/{id}` | Update one |
| `DELETE` | `/api/v1/ai/integrations/{id}` | Delete it and unbind features if the supplier API is gone |
| `PUT` | `/api/v1/ai/features/{feature}` | Bind `{vendor, model}`; empty vendor unbinds |
| `PUT` | `/api/v1/ai/runtime` | Update the shared model limits |

```bash
curl -s -X POST http://[::1]:8000/api/v1/ai/vendors \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"deepseek","label":"DeepSeek",
       "models":[{"id":"deepseek-chat","context_window":128000},
                 {"id":"deepseek-reasoner"}]}'

curl -s -X POST http://[::1]:8000/api/v1/ai/integrations \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"deepseek-internal","label":"DeepSeek API","vendor":"deepseek",
       "kind":"openai","base_url":"https://api.deepseek.com","gateway":false}'

curl -s -X PUT http://[::1]:8000/api/v1/ai/vendors/deepseek/key \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"api_key":"sk-..."}'

curl -s -X POST http://[::1]:8000/api/v1/ai/vendors/deepseek/models/sync \
  -H "Authorization: Bearer $TOKEN"

curl -s -X PUT http://[::1]:8000/api/v1/ai/features/assistant.chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"vendor":"deepseek","model":"deepseek-chat"}'
```

Set `"gateway": true` to expose an OpenAI/Anthropic endpoint at
`/{name}/...`. A caller reaches it with a Winnie gateway key (Gateway routes
page, `data/gateway-keys.yaml`) and the gateway injects the supplier key stored in
the registry. A feature binding
names a model from the supplier catalog; an unknown model is rejected. The
supplier's
internal API is the only thing used for a model call; gateway routes are
separate. A change applies on the next model call, and the arq worker re-reads
the file by modification time.
The console page is documented in
[docs/ai-providers.md](../../docs/ai-providers.md).

## LLM usage

Every internal model call and every call through the gateway appends one JSON
line to a local ledger. The console's **LLM usage** page reads it for token
classes, latency, estimated cost and provider balances. Gateway events also
carry the Winnie key that made the call (`detail.key_id`). The ledger is switched
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

Balances are queried only for integrations whose supplier has a stored key, and
only when the vendor exposes an endpoint:
DeepSeek, OpenRouter, Kimi/Moonshot and SiliconFlow. The gateway injects that
same key, so proxied traffic is both recorded and covered by the balance.

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
binding. It does not seed a model: translation and minutes start unbound.
After that first start, choose models on the AI suppliers page; there is no YAML
fallback for a model feature.

For Doubao ASR, set the `volcengine` supplier's key on the AI providers page
(**API key**). This is the API key from the current Volcengine console, sent as
`X-Api-Key`; the legacy APP ID + Access Token pair is no longer accepted.

For real translation and structured minutes, add a supplier API and a model, set
the supplier's key on the same page, sync its models, and bind both
`meetings.translate` and `meetings.summarize` to the model. Timeouts,
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

The script presents one Winnie gateway key and exercises both wire protocols;
the gateway injects the DeepSeek supplier key it stores:

| Route | Protocol | Path |
|-------|----------|------|
| `deepseek-openai` | openai | `/deepseek-openai/v1/chat/completions` |
| `deepseek-anthropic` | anthropic | `/deepseek-anthropic/v1/messages` |

```bash
# scripts/.env contains WINNIE_GATEWAY_KEY
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
only from the `assistant.chat` binding; a fresh registry leaves it unbound until
a supplier model is configured on the AI suppliers page. Web search uses Bing
RSS by default and needs no key.

The default `gold` skill adds `gold_price` and a five-minute sampler. A move of
at least 1% against the sample closest to an hour ago, or 2.5% over 24 hours,
records a notice and pushes the Winnie app through the same notifier as
`notify_user`; a two-hour cooldown follows an alert. `http://[::1]:8000/api/v1/assistant/gold`
shows the thresholds and last sample, and `POST .../gold/check` samples without
pushing. The `assistant.gold` block sets the schedule, window and thresholds.

The default `weather` skill adds `weather_forecast` and a daily watch. The
watch fetches Open-Meteo for the configured location, applies the rain, snow,
temperature and severe-weather rules, and records a notice only when the advice
changed; the same tool answers weather questions in chat. `http://[::1]:8000/api/v1/assistant/weather`
shows its schedule and last result, and `POST .../weather/check` runs it now.
The `assistant.weather` block sets the location, schedule and thresholds. The
Skills page toggles the whole feature.

Local recall goes through `winnie-index` (`make index-serve`, listening on
`127.0.0.1:8790`). With the daemon down, chat still works and the local-document
tools report why they failed. `GET /api/v1/assistant/documents/search?q=` exposes
the same search for debugging.

The chat loop and the skill registry live in `packages/winnie-chat`; the
assistant composes them with its store, memory, scheduler and the local executor.
Skills are registered by the assistant (core, web, weather, documents, memory,
tasks, local, phone and phone_write). The
console `/skills` page and `GET /api/v1/assistant/skills` show them, and
`PUT /api/v1/assistant/skills/{name}` toggles one; the state is persisted in
`<root>/skills/skills.json`.

Scheduled tasks are created by asking the assistant; there is no file to edit.
The JSON at `<root>/tasks/tasks.json`, the schedule forms and the memory
pipeline are documented in [docs/assistant.md](../../docs/assistant.md).


## Phone data

`/api/v1/phone` receives the batches the Android client uploads and serves them
to the assistant and to any authenticated client. The phone is the only writer
of its own data; the server merges by `(device, external_id)`, soft-deletes what
a full snapshot omits, and applies per-kind retention on every sync.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/devices` | Register a device; upsert by `device_key` |
| `GET` | `/devices`, `/devices/{id}` | Devices and per-kind sync state |
| `POST` | `/devices/{id}/sync` | One kind per request, at most `max_batch_items` items |
| `GET` | `/devices/{id}/ids?kind=&since=&until=` | Ids for a client-side snapshot diff |
| `GET` | `/devices/{id}/commands` | Claim approved writes |
| `POST` | `/devices/{id}/commands/{cid}/result` | Report a write result |
| `DELETE` | `/devices/{id}` | Revoke; `POST /restore` undoes it |
| `DELETE` | `/devices/{id}/data` | Delete every synced record for the device |
| `GET` | `/summary`, `/calendar`, `/calls`, `/messages`, `/contacts`, `/notifications`, `/locations`, `/apps`, `/search` | Queries |
| `POST` | `/commands`, `/commands/{id}/approve`, `/commands/{id}/cancel` | Propose and confirm a write |

Everything requires the bearer session, and every device id is checked against
the authenticated account. Writes are queued, not applied by the API: a
proposed command becomes eligible for the phone only after
`/commands/{id}/approve`, and `phone.command_ttl_seconds` expires it otherwise.
See [docs/phone.md](../../docs/phone.md) for the batch contract, the write
path, retention and the security limits (the database is not encrypted at
rest).


## Push

`/api/v1/push` is the channel the Android foreground service holds open. Every
event is a database row before it is a WebSocket frame, so an offline phone
receives it from the backlog on reconnect.

| Method | Path | Purpose |
|---|---|---|
| `WS` | `/ws?after=&device_id=` | Backlog after the cursor, then live events; auth by bearer header or `?token=` |
| `GET` | `/notifications?after_seq=&device_id=&limit=` | The same backlog over HTTP |
| `GET` | `/status` | Open connections, pending count, last event |
| `POST` | `/test` | Publish a notification to the caller |

Publishers are in-process: the assistant's `notify_user` tool, scheduled task
results, an approved phone command (a device-targeted `sync`) and its result. The
fan-out is per process, so run one API worker or add Redis pub/sub before
scaling out. See [docs/push.md](../../docs/push.md).
