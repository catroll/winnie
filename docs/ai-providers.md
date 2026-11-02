# AI suppliers, models and feature bindings

The registry is the **only** place that selects a model. `config.yaml` no longer
has an `llm` block, `assistant.model`, `meetings.translator` or
`meetings.summarizer`.

The unit a feature binds to is a **model**. A model belongs to a **supplier**;
the supplier is resolved from the model and provides the API endpoint, key and
model list. A feature never selects a supplier or an endpoint.

- Console page: **AI providers** (`/ai` in the admin app). Its **Suppliers** tab
  is this registry; its **LLM usage** tab shows what the bound models cost (see
  [usage.md](./usage.md))
- API prefix: `/api/v1/ai`
- State file: `data/ai.yaml` by default (gitignored)
- Owner: `packages/winnie-llm` (`winnie_llm.registry`); `apps/api` only exposes
  the HTTP console below
- Each supplier's API key is stored in this file with its models. The console
  mirrors every saved key into `packages/winnie-llm/.env` as a backup; that file
  is not a source, except for a gateway-only deployment and a one-time import

## Concepts

A **supplier** is the vendor (`deepseek`, `openai`, `qwen`, `anthropic`,
`volcengine`) and owns:

- one **API endpoint** for internal calls: base URL and protocol;
- one **API key**, stored on the supplier record (write-only on the console API);
- a **model catalog**;
- zero or more **gateway routes** — public `/{name}/...` proxy paths that inject
  the supplier's key for a caller holding a Winnie gateway key.

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

**Sync models** calls the supplier's OpenAI-compatible `GET {base}/models`
with the supplier key, reads `data[].id` (plus `owned_by` and `created`), and
writes the catalog. A model that is still used by a binding or an endpoint
default is kept if the supplier temporarily omits it.
**Models** is the manual fallback for ASR and custom endpoints.

`{base}` is the integration's `base_url` when it already ends in a version
segment (`/v1` for OpenAI, DeepSeek and DashScope, `/v4` for Zhipu), and
`{base_url}/v1` otherwise. Chat calls use the same rule, so configure the API
root: `https://api.deepseek.com/v1`, `https://open.bigmodel.cn/api/paas/v4`.

A key on its own is not enough: sync reads its base URL from the supplier's
**internal endpoint** (an integration with `gateway: false`). A gateway route is
a public proxy path and is ignored here, so a supplier that has only gateway
routes has no **Sync models** button, and its card tells you to add the API and
switch **Gateway route** off. Add the endpoint first — for Qwen that is kind
`openai` and base URL `https://dashscope.aliyuncs.com/compatible-mode/v1` (a
bare `.../compatible-mode` also works; `/v1` is appended once). The catalog
mirrors whatever the supplier lists, so DashScope's resold third-party models
(`antdigital/...`, `kimi/...`) arrive with Qwen's; prune them with **Models** if
the selector becomes noisy. A key or endpoint edited after the API process
started needs a restart before a retry.

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
  route**; routes are grouped under their supplier
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

A `version: 5` registry is migrated to `version: 6`: the stored `api_key_env`
field is removed from every endpoint, and the variable name is derived from the
supplier id at call time. The file becomes `version: 6`.

A `version: 6` registry is migrated to `version: 7`: each supplier's key is
imported from `packages/winnie-llm/.env` onto its record and internal calls stop
reading the environment. The backup file is left exactly as it was. A fresh
registry (a first start, or a deleted `data/ai.yaml`) imports the same backup
once, so wiping the file does not lose the credentials.

## API keys

Every supplier credential is stored in `data/ai.yaml`, on the supplier record,
next to its model catalog. It is written by the console (**AI providers → API
key**, or `PUT /api/v1/ai/vendors/{id}/key`) and read by every internal model
call, the gateway and the balance report from that one place.

The console never returns the value: `GET /api/v1/ai/config` reports
`api_key_present` and `api_key_hint` (the last four characters), which is enough
to tell two keys apart. The value is not in any JSON response or log line.

The variable each supplier is mirrored under:

| Supplier | Backup variable |
|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `qwen` | `DASHSCOPE_API_KEY` |
| `zhipu` | `ZHIPU_API_KEY` |
| `volcengine` | `VOLCENGINE_ASR_API_KEY` |
| any other supplier id | `<SUPPLIER>_API_KEY` |

A key belongs to a **supplier**, not a feature or a feature binding: every model
of that supplier shares it. To use a second account for the same vendor, add a
supplier such as `deepseek-work` and set its key in the console; it is mirrored
as `DEEPSEEK_WORK_API_KEY`.

### The environment backup

Saving a key also writes a copy into `packages/winnie-llm/.env`, keeping the
file's comments, ordering and unrelated lines. The two are kept equal by the
console, but they are not equal in authority:

- **The registry wins.** Editing only the backup changes nothing; the next
  console save overwrites that line again.
- **The backup is readable.** It is the file an operator inspects (mode `0600`)
  and the one a gateway-only deployment (`ai.enabled: false`) injects, because
  that deployment has no registry to read.
- **A pre-upgrade key is imported once.** On the first read of a `version: 6`
  file, each supplier without a stored key takes the backup's value.

`WINNIE_LLM_ENV_FILE` points the backup path elsewhere; the API tests use it so
the checkout's real keys never reach a test.

A gateway route uses the same supplier key: the gateway injects it for a caller
that presents a Winnie gateway key (created on the API keys page). A gateway-only
record is never a feature target; the internal API button/config is separate.

## Changes take effect without a restart

The capability packages are built once, but their provider ports are thin
wrappers that resolve the current binding and runtime on every call. Switching a
model, syncing the catalog or editing the supplier API affects the next
assistant turn, the next meeting pipeline stage and the next live-caption
connection.

The gateway route table is refreshed in the API process on every CRUD request,
including a saved supplier key. An OpenAI or Anthropic gateway route makes
`/{name}/...` reachable immediately; turning it off, disabling or deleting it
removes the path. The separate arq worker reads the file by modification time,
so it also sees a new key without a restart. Rotating a key needs no restart at
all: the next internal call and the next gateway request use the new value.

## HTTP API

All endpoints require the same bearer session as the rest of the console.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/ai/config` | Suppliers, models, endpoints, bindings, runtime |
| `POST` | `/api/v1/ai/vendors` | Create a supplier |
| `PUT` | `/api/v1/ai/vendors/{id}` | Update a supplier label/models |
| `PUT` | `/api/v1/ai/vendors/{id}/key` | Store `{api_key}`; empty clears it; mirrors the env backup |
| `POST` | `/api/v1/ai/vendors/{id}/models/sync` | Fetch the model list from the supplier |
| `POST` | `/api/v1/ai/integrations` | Create the supplier API or a gateway route |
| `PUT` | `/api/v1/ai/integrations/{id}` | Update one |
| `DELETE` | `/api/v1/ai/integrations/{id}` | Delete it; unbind features if the supplier API is gone |
| `PUT` | `/api/v1/ai/features/{feature}` | Bind `{vendor, model}`; empty vendor unbinds |
| `PUT` | `/api/v1/ai/runtime` | Update the shared model limits |

Create a supplier, set its key, sync its models, then bind a feature to a model:

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
    "gateway": false
  }'

curl -s -X PUT http://127.0.0.1:8000/api/v1/ai/vendors/deepseek/key \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"api_key":"sk-..."}'

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

- Back up `data/ai.yaml` with the rest of `data/`. It is small and
  human-readable, and it now holds the supplier credentials: treat it like a
  secrets file, which is why it is written with mode `0600` and gitignored.
- A manually edited file is validated on the next read; an invalid file fails
  loudly with the path and the validation error instead of silently falling
  back. Use the console for normal changes.
- The file is written atomically (temporary file plus rename) with mode `0600`.
- The file is `version: 7`. A `version: 1`–`6` file is migrated in place on the
  next read; back it up first if it contains manual edits.
- Multiple API processes share the file. The process that handled the console
  write refreshes its gateway table immediately; another process only re-reads
  bindings/runtime on the next resolution and does not rebuild its gateway
  table, which is why keeping the API at one worker remains the simple
  deployment. A TTL reload or a shared revision is a follow-up
  ([todo](./todo.md)).
- `volcengine_asr` options are validated by `winnie_llm.asr.VolcengineASRConfig`,
  so ranges and defaults do not drift between the console and the adapter.
- Supplier marks are adapted from the WLB newapi `vendor-icons.js` catalog
  (`DeepSeek`, `OpenAI`, Alibaba/Qwen, Anthropic, ByteDance/Volcengine,
  Zhipu/BigModel — supplier id `zhipu` or `chatglm` — with a monogram
  fallback). They are static inline SVG, so the admin needs no icon
  dependency.
