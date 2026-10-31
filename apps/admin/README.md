# Winnie Admin

A small web management console for a personal Winnie instance. It is built on
[AdminLTE 4](https://adminlte.io) and talks to the existing Winnie API, so it
adds no new backend service.

It is for looking at and operating what the API already knows: your meetings,
their transcripts and minutes, the assistant chat and every conversation it has
recorded, the assistant's scheduled tasks and their run history, and the AI
providers every AI-using feature calls. It is not a recording client and it does
not store vendor API keys.

## What you can do

| Page | What it shows |
|------|---------------|
| **Dashboard** | Meeting count, configured routes, API version and readiness, plus the five most recent meetings |
| **Chat** | A ChatGPT-like conversation with the assistant, plus the full history from every channel: console, phone page, API clients and scheduled task runs. Filter by channel, reopen any transcript including its tool blocks, and send from the console |
| **Meetings** | Paginated list of the signed-in user's meetings with status, source, duration and update time |
| **Meeting detail** | Overview, processing error and stage; transcript with translations; structured minutes; Markdown/TXT export; regenerate minutes; delete |
| **Scheduled tasks** | Assistant-created tasks with schedule, active/paused state, next and last run, last result and error, prompt and webhook target; ignored definitions; scheduler state; a Run history card with every recorded execution (newest first, filterable by task, paginated) showing duration, conversation and errors |
| **LLM usage** | Recorded model calls with token classes, latency, status and cost; breakdowns by day, feature and model; provider balances with a refresh; the configured price table |
| **AI providers** | Add, edit and delete vendor integrations (OpenAI-compatible, Anthropic, Volcengine ASR, deterministic fake); mark an OpenAI/Anthropic provider as a gateway route or internal-only; choose the provider and model for the assistant, transcription, translation and minutes; see gateway paths, internal-only status and whether the environment key is set |
| **Document index** | Status and a search test (chunk-level hits, why a miss happened), editable roots and limits, dry run, build now, skill install, failures |
| **Gateway routes** | Every OpenAI/Anthropic provider exposed as a route, with protocol, upstream, API base and proxy paths |
| **Account** | Profile, session expiry and password change |

The API scopes meetings to the authenticated account, so the console shows the
same data the Android client does for that user.

## Assistant chat and conversation history

`/chat` is the console's chat surface. It calls the same
`POST /api/v1/assistant/chat` route as the phone page, so a conversation is one
Markdown file regardless of where it started. The history sidebar lists all
four channels:

| Channel | Where it came from |
|---------|--------------------|
| **Console** | This page |
| **Mobile web** | `/api/v1/assistant/ui` on a phone |
| **API** | A direct API caller, and files written before channels existed |
| **Scheduled task** | An unattended task run |

The filter is server-side. Selecting a conversation shows its full transcript,
including `tool:` blocks as collapsible trace entries. A console reply is
non-streaming: the page shows a thinking indicator and then re-reads the file so
the recorded tool calls appear. A task-run record is read-only; the API rejects a
chat turn on it with `409`, and the composer is replaced with a notice.

The chat is available when `assistant.enabled` is true in
`apps/api/config.yaml`; otherwise the history request explains why it cannot
load.

## AI providers

`/ai` owns the vendor registry. On first start the API seeds
`data/ai.yaml` from the existing `routes`, `llm`, `meetings` and `assistant`
configuration, then the console is authoritative. Each feature selects one
integration and an optional model:

| Feature | What it drives |
|---|---|
| Assistant chat | Replies, tool calls and memory distillation |
| Meeting transcription | File ASR and live captions |
| Meeting translation | Non-local transcript segments |
| Meeting minutes | Structured summary, decisions and action items |

An OpenAI/Anthropic integration has a separate **Gateway route** switch. With it
on, the provider is reachable at `/{name}/v1/...` and the client supplies the
vendor key; with it off, the provider is internal-only and is called by the
assistant or meetings with `api_key_env`. The seeded `internal-llm` is
internal-only; the seeded routes have no server-side key. A keyless
OpenAI-compatible provider cannot serve an internal feature, so it is not listed
in those feature dropdowns.

A provider change takes effect on the next model call; no API restart is needed.
Deleting a provider resets its features to their `config.yaml` fallback. The
page never receives or stores a vendor key: `api_key_env` is the *name* of an
environment variable, and the table shows whether it is currently set. The full
model, first-start precedence (including the `version: 1` migration) and the HTTP
API are in [docs/ai-providers.md](../../docs/ai-providers.md).

## Quick start

Requires Node.js `^20.19` or `>=22.12` and a running API (`make api` from the
repository root).

```bash
cd apps/admin
npm install
npm run dev
```

If `$HOME` is read-only in your environment, point npm's cache into the
checkout first (this repository already keeps tool caches under `.cache/`):

```bash
npm_config_cache="$(git rev-parse --show-toplevel)/.cache/npm" npm install
```

Open <http://127.0.0.1:5173> and sign in with an account from the API. From the
repository root, `make admin` starts the same dev server after checking that
dependencies are installed.

The dev server proxies `/api`, `/healthz`, `/readyz` and `/routes` to
`http://127.0.0.1:8000`. Override the target when the API runs elsewhere:

```bash
VITE_API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev
```

or put it in `apps/admin/.env` (see `.env.example`).

## Production build

```bash
npm run build       # typecheck, then write dist/
```

Serve `dist/` as a single-page application: unknown paths must fall back to
`index.html`, because the console uses the History API. The web server must
forward these prefixes to the API **on the same origin**:

- `/api/`
- `/healthz`
- `/readyz`
- `/routes`

Same-origin is the supported deployment and needs no CORS configuration on the
API. `VITE_API_BASE_URL` can point the client at a different origin, but the
API has no CORS middleware; adding it would be a separate, deliberate change.

## Sessions and security

- The bearer token is kept in `sessionStorage`. **Remember me** moves it to
  `localStorage`; signing out or a `401` clears both.
- The console never renders a token and never sends one anywhere except the
  Winnie API.
- Changing the password revokes every server-side session, including the one in
  the browser, so the console returns to the login page with a notice.
- Meeting text is rendered as text, not HTML.
- Vendor keys are not entered or stored here. The AI providers page stores the
  *name* of an environment variable per provider and only reports whether the
  API process can see it. Gateway routes show the configured upstreams.

## LLM usage page

`/usage` reads `/api/v1/usage/*` and renders one report per selected period
(24 hours, 7, 30 or 90 days). It has no write path: prices and the ledger root
come from `apps/api/config.yaml`, and provider balances are read from the
vendors named by the AI integrations.

- **Provider balances** are cached for five minutes by default. **Refresh
  balances** forces a query; a failing provider becomes an error on its row.
- **Cost** prefers a provider-reported value and otherwise applies the local
  price table. A call with no usable price is shown as unpriced, with the
  reason in its detail row; a call whose provider returned no usage is shown as
  having no usage, not as costing zero.
- Opening a call's detail renders an extra full-width row, so the table's
  columns never resize.
- Gateway traffic is recorded but its balance belongs to the client's key; the
  card says so.

## Scope

Deliberately not part of this app yet:

- Recording and audio upload (the Android client owns the capture path).
- Account creation and registration (the first account is created through the
  API; later registration is controlled by `auth.allow_registration`).
- Creating, editing or cancelling scheduled tasks. The assistant owns
  `tasks/tasks.json` through the chat tools, so the console only reads it; a
  second writer would also have to synchronise the scheduler's next-run state.
  The task table shows the last persisted result and the Run history card pages
  through `tasks/runs.jsonl` (newest first, filterable by task); the log remains
  the only store.
- Editing the AI provider registry outside the console. `data/ai.yaml` is
  written atomically and validated on load, but the console is the supported
  editor; a hand-edited file is only read.
- Entering vendor API keys in the browser. They stay in the API process
  environment; the console can only choose which variable a provider names.
- Browsing the raw gateway request logs (they stay on disk under `data/logs/`).
  The assistant history is a different record: the conversation Markdown the
  assistant wrote, not a proxy request log. **LLM usage** is a third record:
  token counts and cost, without prompts or responses.
- Editing prices in the browser. They live in `usage.prices` in
  `apps/api/config.yaml`, because a price is deployment billing data and the
  ledger re-prices history from that table.
- Knowledge-base management, which is still planned.

## Stack

| Area | Choice |
|------|--------|
| UI framework | AdminLTE 4.10 with Bootstrap 5.3 |
| App shell | Vite 8, React 19, React Router 7, TypeScript 7 |
| Icons and type | Bootstrap Icons 1.13, Source Sans 3 (self-hosted) |
| API | Winnie's existing FastAPI backend over bearer auth |

There is no custom theme: the layout, login box, cards, small boxes, tabs and
colour-mode control are AdminLTE's. The only application CSS is a full-height
React root and `white-space: pre-line` for transcript text.

## Development checks

```bash
npm run typecheck   # tsc --noEmit
npm run build       # typecheck + Vite production build
```

Behavioral verification is recorded in
[docs/plan.md](../../docs/plan.md#6-verification-snapshot) and
[docs/decisions.md](../../docs/decisions.md).

## License

The console is part of Winnie and is licensed under the repository's
[Apache License 2.0](../../LICENSE).
