# AI suppliers, endpoints, models and feature bindings

The registry is the **only** place that selects a model. `config.yaml` no longer
has an `llm` block, `assistant.model`, `meetings.translator` or
`meetings.summarizer`; those choices live here and are edited in the console.

- Console page: **AI suppliers** (`/ai` in the admin app)
- API prefix: `/api/v1/ai`
- State file: `data/ai.yaml` by default (gitignored)
- No vendor keys are stored: an endpoint names an environment variable

## Concepts

A **supplier** is the vendor (`deepseek`, `openai`, `qwen`, `anthropic`,
`volcengine`, `fake`) and owns two lists:

- **endpoints** — the wire protocol plus its role (gateway route or internal
  provider);
- **models** — the model ids the supplier offers, with display metadata.

One supplier can have several endpoints because protocol and exposure are
independent:

| Endpoint kind | Purpose | Can be a gateway route |
|---|---|---|
| `openai` | Any `/chat/completions` endpoint; assistant, translation, minutes | yes |
| `anthropic` | Anthropic Messages endpoint | yes |
| `volcengine_asr` | Doubao recording-file and streaming ASR | no |
| `fake` | Deterministic offline providers for tests and demos | no |

For example, DeepSeek is one supplier with up to three endpoints: an
OpenAI-compatible gateway route, an Anthropic-compatible gateway route, and an
internal OpenAI-compatible endpoint with a server-side key. The console groups
them under **DeepSeek**; the endpoint rows are role labels, not separate
suppliers.

Whether an endpoint is a public proxy path is the `gateway` field, not the
kind. An OpenAI-compatible endpoint can be:

- a gateway route (`gateway: true`, no server-side key; the client forwards its
  own key);
- an internal provider (`gateway: false`, `api_key_env` set; the assistant and
  meetings call it directly);
- both, if the owner explicitly wants that.

`volcengine_asr` and `fake` are always internal.

A **model** belongs to the supplier, not to one endpoint:

| Field | Meaning |
|---|---|
| `id` | The exact model id sent on the request (`deepseek-chat`) |
| `label` | Console label; defaults to the id |
| `context_window` | Optional context size shown in the binding select |
| `max_output_tokens` | Optional per-model output cap shown in the console |
| `notes` | Optional free text |

The endpoint's `default_model` is used when a feature binding leaves the model
empty. A binding may choose any model from the supplier catalog; an unknown id
is rejected with `model ... is not in the <supplier> catalog`. The runtime
limits below are shared and separate from per-model metadata.

A **feature binding** selects an internal endpoint and optionally one of its
supplier's models:

| Feature | Uses | Model |
|---|---|---|
| `assistant.chat` | Assistant replies, tool calls and memory distillation | yes |
| `meetings.transcribe` | File ASR and live captions | no |
| `meetings.translate` | Segment translation | yes |
| `meetings.summarize` | Structured minutes | yes |

An unbound feature has **no provider** and fails with a clear
`feature ... has no provider` error; there is no YAML fallback. Deleting an
endpoint unbinds the features that referenced it, so choose a replacement
before using them again. A fresh registry binds every model feature to `fake`
and ASR to its configured default, so the app is usable before a real provider
is added.

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

They apply to the assistant, translation and minutes. The model and endpoint
come from the binding; the runtime only supplies limits. Edit them in the
**Model runtime** card or with `PUT /api/v1/ai/runtime`; a change applies on the
next call.

## First start

On the first start with `ai.enabled: true`, Winnie writes `data/ai.yaml` from the
remaining YAML inputs:

- every entry under `routes` becomes an `openai` or `anthropic` endpoint with
  `gateway: true` and `api_key_env: ""` (gateway paths forward the client key);
  endpoints are grouped under their supplier (`deepseek-openai` →
  **DeepSeek**);
- `meetings.volcengine` supplies the endpoint/options for a `volcengine-asr`
  endpoint; `meetings.asr_provider` selects the initial ASR binding
  (`volcengine` or `fake`); its model name is added to the Volcengine catalog;
- a `fake` endpoint is always added;
- `assistant.chat`, `meetings.translate` and `meetings.summarize` start bound to
  `fake`; the owner adds a supplier endpoint and models, then rebinds them.

There is no `llm` block to import and no `internal-llm` generated on a fresh
start. An existing registry that already has an `internal-llm` (from the v1
migration) keeps it as a normal internal endpoint under its supplier; it can be
edited, rebound or deleted like any other endpoint.

`ai.enabled` defaults to `false` in a programmatic `Settings()` so tests and
embedders never read or write the file. The shipped `apps/api/config.yaml`
enables it.

```yaml
ai:
  enabled: true
  # Relative to apps/api/, like every other root.
  path: ../../data/ai.yaml
```

### v1 → v2 → v3 migration

A `version: 1` registry is migrated on first read. v1 reused a gateway route as
the internal LLM, so its server-side key sat on a public path. The migration:

- adds `gateway: false` to non-gateway kinds and `gateway: true` to the old
  combined route;
- finds an OpenAI integration that has an `api_key_env` and is referenced by a
  binding (the old combined record), creates a separate `internal-llm` from its
  endpoint/key/model, clears the route key/model and repoints the bindings;
- replaces only auto-generated labels and writes `version: 2`.

A `version: 2` registry is grouped in place on the next read: each integration
gets a supplier id inferred from its name and base URL, a supplier record is
created if needed, and every endpoint default / bound model is added to that
supplier's model catalog. Gateway routes and internal endpoints keep their ids
and names, so bindings and public paths do not change. The file becomes
`version: 3`.

## API keys

The registry never stores a key. `api_key_env` is the *name* of an environment
variable, for example `DEEPSEEK_API_KEY`; the API process reads the value when it
builds a provider. The console shows **set in environment** or **missing from
environment** for each endpoint but never renders the value.

A value pasted into the `api_key_env` field is rejected by validation. Set the
key in the process environment (directly or through `apps/api/.env`) and restart
the API or worker: a running process keeps the environment it was started with.

Gateway paths still forward the client's own vendor key. The environment
variable matters for internal jobs and the assistant, which call the provider
directly. An OpenAI-compatible endpoint with an empty `api_key_env` is
gateway-only and cannot be bound to an internal feature; the console filters it
out of those dropdowns.

New endpoints get a conventional suggestion from their name
(`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `DASHSCOPE_API_KEY`,
`ANTHROPIC_API_KEY`, `VOLCENGINE_ASR_API_KEY`); the field remains editable.

## Changes take effect without a restart

The capability packages are built once, but their provider ports are thin
wrappers that resolve the current binding and runtime on every call. Adding an
endpoint, adding a model or switching a binding affects the next assistant
turn, the next meeting pipeline stage and the next live-caption connection.

The gateway route table is refreshed in the API process on every CRUD request.
An OpenAI or Anthropic endpoint with `gateway: true` makes
`/{name}/v1/...` reachable immediately; turning the switch off, disabling or
deleting it removes the path. Internal-only endpoints never appear there. The
separate arq worker reads the file by modification time, so it also sees the
change without a restart.

## HTTP API

All endpoints require the same bearer session as the rest of the console.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/ai/config` | Suppliers, endpoints, models, bindings, runtime, kind metadata |
| `POST` | `/api/v1/ai/vendors` | Create a supplier and its model catalog |
| `PUT` | `/api/v1/ai/vendors/{id}` | Update a supplier label/models |
| `POST` | `/api/v1/ai/integrations` | Create an endpoint under a supplier |
| `PUT` | `/api/v1/ai/integrations/{id}` | Update an endpoint |
| `DELETE` | `/api/v1/ai/integrations/{id}` | Delete it and unbind its features |
| `PUT` | `/api/v1/ai/features/{feature}` | Bind a feature (`integration_id: null` unbinds) |
| `PUT` | `/api/v1/ai/runtime` | Update the shared model limits |

Create a supplier with two models, then an internal endpoint under it:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ai/vendors \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "deepseek",
    "label": "DeepSeek",
    "models": [
      {"id": "deepseek-chat", "label": "DeepSeek Chat",
       "context_window": 128000, "max_output_tokens": 8192},
      {"id": "deepseek-reasoner", "label": "DeepSeek Reasoner"}
    ]
  }'

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
    "default_model": "deepseek-chat",
    "enabled": true,
    "gateway": false
  }'
```

The gateway routes for the same supplier are separate endpoint records with
`"gateway": true`; the console groups all of them under one supplier.

Point the assistant at the endpoint; `model` must be in the supplier catalog:

```bash
curl -s -X PUT http://127.0.0.1:8000/api/v1/ai/features/assistant.chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"integration_id":"<id>","model":"deepseek-reasoner"}'
```

Unbind it (the feature will report that it has no provider):

```bash
curl -s -X PUT http://127.0.0.1:8000/api/v1/ai/features/assistant.chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"integration_id":null,"model":""}'
```

Tune the shared limits:

```bash
curl -s -X PUT http://127.0.0.1:8000/api/v1/ai/runtime \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"timeout_seconds":90,"max_output_tokens":2048,"temperature":0.3,
       "max_retries":2,"retry_backoff_seconds":1,
       "translation_batch_chars":6000,"summary_chunk_chars":24000}'
```

## Operational notes

- Back up `data/ai.yaml` with the rest of `data/`. It is small and human-readable.
- A manually edited file is validated on the next read; an invalid file fails
  loudly with the path and the validation error instead of silently falling
  back. Use the console for normal changes.
- The file is written atomically (temporary file plus rename) with mode `0600`.
- The file is `version: 3`. A `version: 1` or `version: 2` file is migrated in
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
