# AI suppliers, models and feature bindings

The registry is the **only** place that selects a model. `config.yaml` no longer
has an `llm` block, `assistant.model`, `meetings.translator` or
`meetings.summarizer`.

The unit a feature binds to is a **model**. A model belongs to a **supplier**;
the supplier is resolved from the model and provides the API endpoint, key and
model list. A feature never selects a supplier or an endpoint.

- Console page: **AI suppliers** (`/ai` in the admin app)
- API prefix: `/api/v1/ai`
- State file: `data/ai.yaml` by default (gitignored)
- No vendor keys are stored: the supplier API names an environment variable

## Concepts

A **supplier** is the vendor (`deepseek`, `openai`, `qwen`, `anthropic`,
`volcengine`) and owns:

- one **API endpoint** for internal calls: base URL, key variable and protocol;
- a **model catalog**;
- zero or more **gateway routes** — public `/{name}/v1/...` proxy paths that
  forward the client's own vendor key.

The API endpoint and the gateway routes are different roles, but they are not
separate suppliers and they are not feature targets. The console shows one
supplier header with its icon, API status, models and gateway-route count; the
server-side endpoint is edited with **API**, not with a provider dropdown in a
feature binding. There is no "Internal (OpenAI-compatible)" row.

A **model** belongs to the supplier:

| Field | Meaning |
|---|---|
| `id` | The exact model id sent on the request (`deepseek-chat`) |
| `label` | Console label; defaults to the id |
| `owned_by` | Owner reported by the supplier `/models` API |
| `created` | Creation timestamp reported by the supplier, when present |
| `context_window` | Optional context size shown in the model selector |
| `max_output_tokens` | Optional per-model output cap |
| `notes` | Optional free text |

**Sync models** calls the supplier's OpenAI-compatible
`GET {base_url}/v1/models` with the supplier key, reads `data[].id` (plus
`owned_by` and `created`), and writes the catalog. A model that is still used by
a binding or an endpoint default is kept if the supplier temporarily omits it.
**Models** is the manual fallback for ASR and custom endpoints.

A **feature binding** selects a supplier and a model:

| Feature | Uses |
|---|---|
| `assistant.chat` | Assistant replies, tool calls and memory distillation |
| `meetings.transcribe` | File ASR and live captions |
| `meetings.translate` | Segment translation |
| `meetings.summarize` | Structured minutes |
| `news.extract` / `news.write` / `news.review` | News extraction, drafting and review |

An unbound feature has **no model** and fails with a clear error; there is no
YAML fallback. A fresh registry leaves every model feature unbound until the
owner configures a supplier model. Deleting the supplier API or removing a model
that a binding uses is rejected or unbinds the feature with a message; choose a
replacement model first.

## Model runtime

The shared limits for every OpenAI-compatible call are stored in the registry,
not in `config.yaml`:

| Field | Default | Meaning |
|---|---|---|
| `timeout_seconds` | `120` | Per-call timeout |
| `max_output_tokens` | `4096` | Response cap |
| `temperature` | `0.2` | Sampling temperature |
| `max_retries` | `2` | Retries for transport/retryable HTTP failures |
| `retry_backoff_seconds` | `1.0` | Base backoff |
| `translation_batch_chars` | `6000` | Translation batch size |
| `summary_chunk_chars` | `24000` | Transcript chunk size for minutes |

They apply to the assistant, translation, minutes and news. The model comes from
the feature binding; the supplier supplies the endpoint. Edit the limits in the
**Model runtime** card or with `PUT /api/v1/ai/runtime`; a change applies on the
next call.

## First start

On the first start with `ai.enabled: true`, Winnie writes `data/ai.yaml` from the
remaining YAML inputs:

- every entry under `routes` becomes an `openai` or `anthropic` **gateway
  route** with `api_key_env: ""`; routes are grouped under their supplier
  (`deepseek-openai` → **DeepSeek**);
- `meetings.volcengine` supplies the endpoint/options for a `volcengine-asr`
  supplier API; `meetings.asr_provider: volcengine` seeds the ASR binding to
  `bigmodel`; otherwise ASR starts unbound;
- no fake supplier is added; every model feature starts unbound until the owner
  adds a supplier API, syncs its models and binds the feature.

There is no `llm` block to import and no `internal-llm` generated on a fresh
start. An existing registry that already has an `internal-llm` (from the v1
migration) keeps it as the DeepSeek supplier API; it can be edited or removed
like any other supplier API.

`ai.enabled` defaults to `false` in a programmatic `Settings()` so tests and
embedders never read or write the file. The shipped `apps/api/config.yaml`
enables it.

```yaml
ai:
  enabled: true
  # Relative to apps/api/, like every other root.
  path: ../../data/ai.yaml
```

### v1 → v2 → v3 → v4 migration

A `version: 1` registry is migrated on first read. v1 reused a gateway route as
the internal LLM, so its server-side key sat on a public path. The migration:

- adds `gateway: false` to non-gateway kinds and `gateway: true` to the old
  combined route;
- finds an OpenAI integration that has an `api_key_env` and is referenced by a
  binding, creates a separate `internal-llm` from its endpoint/key/model, clears
  the route key/model and repoints the bindings;
- replaces only auto-generated labels and writes `version: 2`.

A `version: 2` registry is grouped in place: each integration gets a supplier id
inferred from its name and base URL, a supplier record is created if needed, and
every endpoint default / bound model is added to the supplier catalog. The file
becomes `version: 3`.

A `version: 3` registry is converted to model-first bindings: every
`integration_id` binding becomes `vendor` + `model` (`model` falls back to the
endpoint default). The integration id is cleared, so the console cannot bind a
feature to an endpoint again. The file becomes `version: 4`.

A `version: 4` registry is cleaned up: the seeded fake integration and vendor are
removed, bindings to them are cleared, and features without a configured model
stay unbound. The file becomes `version: 5`. No fake supplier is created again.

## API keys

The registry never stores a key. `api_key_env` is the *name* of an environment
variable, for example `DEEPSEEK_API_KEY`; the API process reads the value when it
calls the supplier. The console shows the variable and whether it is set, but
never renders the value.

A value pasted into the `api_key_env` field is rejected by validation. Set the
key in the process environment (directly or through `apps/api/.env`) and restart
the API or worker: a running process keeps the environment it was started with.

The key belongs to the **supplier API**, not to a feature. A feature bound to a
model inherits the key of that model's supplier; two features using the same
supplier share it. To use a different key, add another supplier entry (for
example `deepseek-work`), give it its own `api_key_env`, and bind the relevant
features to a model from that supplier.

Gateway paths still forward the client's own vendor key. The environment
variable matters for internal jobs and the assistant, which call the supplier
directly. A gateway-only record is never a feature target; the internal API
button/config is separate.

New supplier APIs get a conventional suggestion from their name
(`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `DASHSCOPE_API_KEY`,
`ANTHROPIC_API_KEY`, `VOLCENGINE_ASR_API_KEY`); the field remains editable.

## Changes take effect without a restart

The capability packages are built once, but their provider ports are thin
wrappers that resolve the current binding and runtime on every call. Switching a
model, syncing the catalog or editing the supplier API affects the next
assistant turn, the next meeting pipeline stage and the next live-caption
connection.

The gateway route table is refreshed in the API process on every CRUD request.
An OpenAI or Anthropic gateway route makes `/{name}/v1/...` reachable
immediately; turning it off, disabling or deleting it removes the path. The
separate arq worker reads the file by modification time, so it also sees the
change without a restart.

## HTTP API

All endpoints require the same bearer session as the rest of the console.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/ai/config` | Suppliers, models, endpoints, bindings, runtime |
| `POST` | `/api/v1/ai/vendors` | Create a supplier |
| `PUT` | `/api/v1/ai/vendors/{id}` | Update a supplier label/models |
| `POST` | `/api/v1/ai/vendors/{id}/models/sync` | Fetch the model list from the supplier |
| `POST` | `/api/v1/ai/integrations` | Create the supplier API or a gateway route |
| `PUT` | `/api/v1/ai/integrations/{id}` | Update one |
| `DELETE` | `/api/v1/ai/integrations/{id}` | Delete it; unbind features if the supplier API is gone |
| `PUT` | `/api/v1/ai/features/{feature}` | Bind `{vendor, model}`; empty vendor unbinds |
| `PUT` | `/api/v1/ai/runtime` | Update the shared model limits |

Create a supplier, sync its models, then bind a feature to a model:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ai/vendors \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"deepseek","label":"DeepSeek","models":[]}'

curl -s -X POST http://127.0.0.1:8000/api/v1/ai/integrations \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "deepseek-internal",
    "label": "DeepSeek API",
    "vendor": "deepseek",
    "kind": "openai",
    "base_url": "https://api.deepseek.com/v1",
    "api_key_env": "DEEPSEEK_API_KEY",
    "gateway": false
  }'

curl -s -X POST http://127.0.0.1:8000/api/v1/ai/vendors/deepseek/models/sync \
  -H "Authorization: Bearer $TOKEN"

curl -s -X PUT http://127.0.0.1:8000/api/v1/ai/features/assistant.chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"vendor":"deepseek","model":"deepseek-chat"}'
```

Gateway routes are separate endpoint records with `"gateway": true`; they are
not part of a feature binding. The console renders them as route rows under the
supplier.

## Operational notes

- Back up `data/ai.yaml` with the rest of `data/`. It is small and human-readable.
- A manually edited file is validated on the next read; an invalid file fails
  loudly with the path and the validation error instead of silently falling
  back. Use the console for normal changes.
- The file is written atomically (temporary file plus rename) with mode `0600`.
- The file is `version: 5`. A `version: 1`, `2`, `3` or `4` file is migrated in
  place on the next read; back it up first if it contains manual edits.
- Multiple API processes share the file. The process that handled the console
  write refreshes its gateway table immediately; another process only re-reads
  bindings/runtime on the next resolution and does not rebuild its gateway
  table, which is why keeping the API at one worker remains the simple
  deployment. A TTL reload or a shared revision is a follow-up
  ([todo](./todo.md)).
- `volcengine_asr` options are validated by the capability's own
  `VolcengineASRConfig`, so ranges and defaults do not drift between the console
  and the adapter.
- Supplier marks are adapted from the WLB newapi `vendor-icons.js` catalog
  (`DeepSeek`, `OpenAI`, Alibaba/Qwen, Anthropic, ByteDance/Volcengine, with a
  monogram fallback). They are static inline SVG, so the admin needs no icon
  dependency.
