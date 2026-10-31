# AI providers and feature bindings

The console owns the list of AI providers Winnie may call and which provider
each AI-using feature uses. The state lives in a small YAML file, not in
`config.yaml`, so it can be changed from the browser without editing comments
or restarting the API.

- Console page: **AI providers** (`/ai` in the admin app)
- API prefix: `/api/v1/ai`
- State file: `apps/api/../../data/ai.yaml` by default (gitignored)
- No vendor keys are stored: an integration names an environment variable

## Concepts

An **integration** is one vendor endpoint. It has a kind:

| Kind | Purpose | Gateway route |
|---|---|---|
| `openai` | Any `/chat/completions` endpoint; assistant, translation, minutes | yes |
| `anthropic` | Anthropic Messages endpoint | yes |
| `volcengine_asr` | Doubao recording-file and streaming ASR | no |
| `fake` | Deterministic offline providers for tests and demos | no |

The `name` is a lowercase slug (letters, digits and dashes, up to 63 characters)
because an OpenAI or Anthropic integration becomes the gateway path prefix
`/{name}/v1/...`. It cannot be changed in the console after creation; the API
still accepts a rename, so changing it outside the console also changes the
public path.

A **feature binding** selects the integration (and optionally a model
override) for one feature:

| Feature | Uses | Model |
|---|---|---|
| `assistant.chat` | Assistant replies, tool calls and memory distillation | yes |
| `meetings.transcribe` | File ASR and live captions | no |
| `meetings.translate` | Segment translation | yes |
| `meetings.summarize` | Structured minutes | yes |

A binding with no integration inherits the value from `config.yaml`. Deleting an
integration clears the bindings that referenced it back to that fallback.

## First start and precedence

On the first start with `ai.enabled: true`, Winnie imports the current
configuration into `data/ai.yaml`:

- every entry under `routes` becomes an `openai` or `anthropic` integration;
- a route whose base URL matches `llm.base_url` becomes the internal LLM
  integration (otherwise a new `internal-llm` is added);
- `meetings.asr_provider: volcengine` adds a `volcengine-asr` integration;
- `assistant.model`, `meetings.translator` and `meetings.summarizer` select the
  initial bindings;
- a `fake` integration is always added.

After that file exists, the console is authoritative. This keeps an upgrade
non-breaking (the first start reproduces the old behaviour) while avoiding a
second source of truth afterwards. To deliberately return a feature to the YAML
behaviour, choose **Inherit from config.yaml** in its binding row.

`ai.enabled` defaults to `false` in a programmatic `Settings()` so tests and
embedders never read or write the file. The shipped `apps/api/config.yaml`
enables it.

```yaml
ai:
  enabled: true
  # Relative to apps/api/, like every other root.
  path: ../../data/ai.yaml
```

## API keys

The registry never stores a key. `api_key_env` is the *name* of an environment
variable, for example `DEEPSEEK_API_KEY`; the API process reads the value when it
builds a provider. The console shows **set in environment** or **missing from
environment** for each integration but never renders the value.

A value pasted into the `api_key_env` field is rejected by validation. Set the
key in the process environment (directly or through `apps/api/.env`) and restart
the API: a running process keeps the environment it was started with.

Gateway paths still forward the client's own vendor key. The environment
variable matters for internal jobs and the assistant, which call the provider
directly.

## Changes take effect without a restart

The capability packages are built once, but their provider ports are thin
wrappers that resolve the current binding on every call. Adding a provider,
changing a model or switching a binding affects the next assistant turn, the
next meeting pipeline stage and the next live-caption connection.

The gateway route table is refreshed in the API process on every CRUD request.
Adding an OpenAI or Anthropic integration makes `/{name}/v1/...` reachable
immediately; disabling or deleting it removes the path. The separate arq worker
reads the file by modification time, so it also sees the change without a
restart.

## HTTP API

All endpoints require the same bearer session as the rest of the console.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/ai/config` | Integrations, feature bindings, config fallback, kind metadata |
| `POST` | `/api/v1/ai/integrations` | Create an integration |
| `PUT` | `/api/v1/ai/integrations/{id}` | Update an integration |
| `DELETE` | `/api/v1/ai/integrations/{id}` | Delete it and clear its bindings |
| `PUT` | `/api/v1/ai/features/{feature}` | Bind a feature (`integration_id: null` inherits config) |

Create an OpenAI-compatible provider:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ai/integrations \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "deepseek",
    "label": "DeepSeek",
    "kind": "openai",
    "base_url": "https://api.deepseek.com",
    "api_key_env": "DEEPSEEK_API_KEY",
    "default_model": "deepseek-chat",
    "enabled": true
  }'
```

Point the assistant at it (the model falls back to `default_model`):

```bash
curl -s -X PUT http://127.0.0.1:8000/api/v1/ai/features/assistant.chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"integration_id":"<id>","model":""}'
```

Return the feature to `config.yaml`:

```bash
curl -s -X PUT http://127.0.0.1:8000/api/v1/ai/features/assistant.chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"integration_id":null,"model":""}'
```

## Operational notes

- Back up `data/ai.yaml` with the rest of `data/`. It is small and human-readable.
- A manually edited file is validated on the next read; an invalid file fails
  loudly with the path and the validation error instead of silently falling
  back. Use the console for normal changes.
- The file is written atomically (temporary file plus rename) with mode `0600`.
- Multiple API processes share the file. The process that handled the console
  write refreshes its gateway table immediately; another process only re-reads
  feature bindings on the next provider resolution and does not rebuild its
  gateway table, which is why keeping the API at one worker remains the simple
  deployment. A TTL reload or a shared revision is a follow-up
  ([todo](./todo.md)).
- `volcengine_asr` options are validated by the capability's own
  `VolcengineASRConfig`, so ranges and defaults do not drift between the console
  and the adapter.
