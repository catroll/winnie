# Winnie Admin

A small web management console for a personal Winnie instance. It is built on
[AdminLTE 4](https://adminlte.io) and talks to the existing Winnie API, so it
adds no new backend service.

It is for looking at and operating what the API already knows: your meetings,
their transcripts and minutes, the assistant chat and every conversation it has
recorded, the assistant's scheduled tasks and their run history, the data the
Android client synced from the phone, and the AI providers every AI-using
feature calls. It is not a recording client and it does not store vendor API
keys.

## What you can do

| Page | What it shows |
|------|---------------|
| **Dashboard** | Meeting count, configured routes, API version and readiness, plus the five most recent meetings |
| **Chat** | A ChatGPT-like conversation with the assistant, plus the full history from every channel: console, phone page, API clients and scheduled task runs. Filter by channel, reopen any transcript including its tool blocks, and send from the console — typed, or as a hands-free voice conversation with spoken replies |
| **Meetings** | Paginated list of the signed-in user's meetings with status, source, duration and update time |
| **Phone data** | A card per synced device with model, OS, phone number, carrier and network type, WiFi SSID, IP address, SIM, security patch, scopes, per-kind record counts, battery/network/storage, last sync and sync errors, plus revoke, restore and delete-server-data actions; tabs for calendar, calls, SMS, contacts, notifications, location and apps with a time window, text filter, call direction, unread-only and a result limit; SMS adds received/sent and hide filters for 106 service numbers, five-digit short numbers and specific numbers, and opens a number's whole conversation at the clicked message; the location tab shows the resolved 省/市/区/具体位置 next to the coordinates and can fill in addresses for stored fixes; a cross-kind search; the assistant action queue with status, result and error |
| **Meeting detail** | Overview, processing error and stage; transcript with translations; structured minutes; Markdown/TXT export; regenerate minutes; delete |
| **Scheduled tasks** | Assistant-created tasks with schedule, active/paused state, next and last run, last result and error, prompt and webhook target; ignored definitions; scheduler state; a Run history card with every recorded execution (newest first, filterable by task, paginated) showing duration, conversation and errors |
| **Skills** | Registered chat skills with title, description, tools, permissions, prompt instructions, availability and scheduled-run safety. Enable or disable a skill; a disabled skill's tools and instructions are absent from the next chat turn. The core skill is always on |
| **AI providers** | Two tabs. **Suppliers** manages suppliers, their internal API and model catalog, and gateway routes; choose one model per assistant/transcription/translation/minutes/news feature; sync models from the supplier; see whether `winnie-llm/.env` supplies the supplier key. **LLM usage** shows recorded model calls with token classes, latency, status and cost, breakdowns by day, feature and model, provider balances with a refresh, and the configured price table |
| **Schedule** | One week of the owner's Markdown schedule: the seven day sections as tabs with their events, times, tags and links, plus the week-level items; the add form sits in each day's tab (week items keep their own), its content is a textarea whose first line is the title and whose remaining lines become the event's Markdown note; check items off, edit text and time, delete them, and move between weeks. Pre-2024 files render read-only |
| **Document index** | Status and a search test (chunk-level hits, why a miss happened), editable roots and limits, dry run, build now, skill install, failures |
| **Gateway routes** | Every OpenAI/Anthropic provider exposed as a route, with protocol, upstream, proxy prefix and whether its supplier credential is present. Administrator-only |
| **API keys** | The signed-in account's Winnie gateway keys: label, key id, route allowlist, creation time and revocation, plus a one-time secret when a key is created. An administrator can switch the list to every account's keys and revoke any of them |
| **Users** | Admin-only account list with search, role/status filters, pagination, user creation with a one-time temporary password, password reset, role changes, and activate/deactivate. The last active admin and self-deactivation are protected |
| **Account** | Profile, session expiry and password change |

The API scopes meetings to the authenticated account, so the console shows the
same data the Android client does for that user.

## Phone data

`/phone` reads the copy the Android client uploaded through
`/api/v1/phone`; it never talks to the phone. A card per device shows its model,
OS and app version, the scopes its owner enabled, how many records exist of each
kind, its battery, network and free storage, the last sync and any per-kind sync
error.

Below the cards:

- **Search across everything** sends one query to `/api/v1/phone/search` and
  merges calendar, calls, SMS, contacts, notifications, location and apps by
  time. A location fix appears only when the query matches its coordinates or
  provider.
- The **tabs** load one kind at a time with a window (24 hours to all time), a
  text filter where the kind supports one, a call direction, an unread-only
  switch for SMS, and a limit of 50–500. The API returns the newest N records;
  the footer says so rather than implying pagination.
- The **SMS tab** filters by received/sent and hides the numbers that write
  most: checkboxes drop `106` service numbers and five-digit short numbers,
  and removable chips hide specific numbers or prefixes; picking a recent
  sender is one click. Clicking a message opens the whole conversation with
  that number, scrolls to the clicked message and rings it; sent and received
  bubbles sit on opposite sides. `/messages/thread` merges the `+86` and plain
  spellings of one number.
- **Assistant actions** lists the deferred writes the assistant proposed: kind,
  summary, status (`proposed` to `succeeded`/`failed`/`expired`), the result or
  error, and when it was approved or completed.

**Delete server data** removes every synced record for the device and resets its
sync state; the phone stays registered and uploads again on its next sync.
**Revoke** blocks the device at registration until **Restore**; a revoked phone
cannot re-register itself. Both actions ask for confirmation. The page cannot
edit a record: the phone owns the data and would overwrite a console edit on the
next sync.

The page is shown when `phone.enabled` is true in `apps/api/config.yaml`;
otherwise the API returns 503 and the page reports why.

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

A reply is rendered as Markdown: headings, lists and task items, fenced code,
tables, quotes, links and emphasis. The rendering is the console's own — `marked`
only parses, the tokens become React elements, images are not loaded and a
`javascript:` link becomes plain text. The owner's own turn is shown as typed, so
a `#` or `-` at the start of a line stays a keystroke; tool output stays a `<pre>`
block because it is data, not prose.

When the assistant's local tools are enabled, a `run_command` or an overwrite
appears in the transcript as a `PENDING` tool block. A non-whitelisted action is
first sent to the LLM reviewer; a denial appears as `REVIEW DENIED` and nothing
runs. An allowed action still changes nothing until the user replies with an
explicit confirmation, after which the assistant calls `confirm_action` on the
next turn. The command output, the file write and any errors appear in the same
trace.

The chat is available when `assistant.enabled` is true in
`apps/api/config.yaml`; otherwise the history request explains why it cannot
load.

### Voice conversation

The microphone button beside Send starts a voice conversation and replaces the
composer with a status bar. One turn is: speak, pause, and the silence ends it;
the audio is transcribed by the same ASR binding the phone uses; the text is
sent as a normal chat turn; the reply is spoken; the microphone opens again for
the next turn. **Cancel** ends the conversation, **Send now** skips the wait for
a pause, and **Read replies aloud** (remembered between visits) turns only the
speech off.

- The browser captures 16 kHz mono WAV through an AudioWorklet and uploads it
  to `POST /api/v1/assistant/transcribe`; the console never sees a vendor key.
- A turn with no speech, a stalled microphone, a refused permission and an ASR
  outage each get their own message, and the first two upload nothing.
- `getUserMedia` exists only in a secure context: the console must be on
  `localhost` or HTTPS, and the bar says so when it is not.
- Spoken replies use the browser's own voices, so voice quality is the
  platform's and nothing is billed per character. The spoken text is a
  speech-optimised reduction of the Markdown reply (code blocks and URLs are
  dropped); the full reply stays on screen and the Stop button ends the speech
  without ending the conversation.

The conversation is recorded under the **Console** channel like any other, so a
voice turn can be reopened later from the history.

## Skills

`/skills` lists the skill registry served by the assistant. A skill is a named
bundle of tools, prompt instructions and permissions: web search, the document
index, long-term memory, scheduled tasks, local files and commands, and the
always-on core. The page shows which service backs each skill and whether it is
available; a switch calls `PUT /api/v1/assistant/skills/{name}` and persists the
choice in `skills/skills.json` under the assistant root.

A disabled skill is removed from the model's tool schemas and its prompt
instructions are no longer added, so the next chat turn cannot use it. The
local-execution skill still goes through the LLM review, whitelist and
confirmation flow; disabling the skill only removes it from chat. The core
skill cannot be disabled.

## AI providers

`/ai` owns the supplier registry and is the **only** model choice: `config.yaml`
has no `llm` block, `assistant.model`, `meetings.translator` or
`meetings.summarizer`. On first start the API writes `data/ai.yaml` from the
gateway routes and the ASR defaults. No fake supplier ships: model features
start unbound until a supplier API and model are configured.

The page groups records by **supplier** (DeepSeek, OpenAI, Qwen, Anthropic,
Volcengine). A supplier owns:

- one **API** for internal model calls (base URL/key/protocol);
- **models** — the catalog shown in the feature model select, synced from the
  supplier `/models` API or edited with **Models**;
- **gateway routes** — public proxy paths, shown under the supplier but never
  used by a feature.

Each feature selects exactly one **model**; the supplier is resolved from it.
There is no provider/endpoint dropdown in the feature table:

| Feature | What it drives |
|---|---|
| Assistant chat | Replies, tool calls and memory distillation |
| Meeting transcription | File ASR and live captions |
| Meeting translation | Non-local transcript segments |
| Meeting minutes | Structured summary, decisions and action items |

A gateway route is a separate record with a `Gateway route` switch. It is
reachable at `/{name}/...` and injects that supplier's key; it is never a
feature target. The supplier API is edited with **API**; its key is read by
`winnie-llm` from `packages/winnie-llm/.env`, where the variable is derived from
the supplier id. A route whose variable is not set shows a warning and answers
503. Callers authenticate with a **Winnie gateway key**, created on the Gateway
routes page and stored as a digest in `data/gateway-keys.yaml`.

**Sync models** fetches the supplier's `/models` list with that key; **Models**
is the manual fallback. Supplier marks are inline SVGs adapted from the WLB
newapi `vendor-icons.js` catalog.

The **Model runtime** card edits the shared timeout, token cap, temperature,
retry and chunk limits. They apply to the next call. A model/supplier change
takes effect on the next model call; no API restart is needed. Removing the
supplier API unbinds its features; there is no `config.yaml` fallback, so choose
a replacement before using them again. The page never receives or stores a
vendor key: `winnie-llm` derives the variable from the supplier id, reads it from
`packages/winnie-llm/.env`, and the header shows the variable and whether it is
set. The full model, first-start behaviour (including the `version: 1`–`6`
migration) and the HTTP API are in
[docs/ai-providers.md](../../docs/ai-providers.md).

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
dependencies are installed. `make start` starts the API, index and console
together; see [docs/operations.md](../../docs/operations.md).

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

- Accounts have an explicit `admin`/`user` role. The API enforces every
  operator route with `403`; hiding a menu item is not the access control. A
  `403` makes the console re-read `/auth/me` so a demotion reaches the UI.
- The sidebar has two groups: **Workspace** (Chat, Meetings, Phone data,
  Scheduled tasks, Skills, API keys, Account) and **Administration** (Dashboard,
  Schedule, Users, AI providers, Gateway routes, Document index, News). A normal
  user sees only Workspace, lands on `/chat`, and gets a 403 panel if they open
  an admin URL directly.
- The bearer token is kept in `sessionStorage`. **Remember me** moves it to
  `localStorage`; signing out or a `401` clears both.
- The console never renders a token and never sends one anywhere except the
  Winnie API.
- Changing the password revokes every server-side session, including the one in
  the browser, so the console returns to the login page with a notice.
- Meeting text is rendered as text, not HTML.
- Vendor keys are not entered or stored here. The AI providers page shows the
  variable `winnie-llm` derives from each supplier and whether
  `packages/winnie-llm/.env` supplies it. The API keys page shows the caller's
  own keys; the secret is shown once at creation and only its digest is stored.
  A key can only be revoked by its owner or an administrator.

## LLM usage tab

The **LLM usage** tab of `/ai` (`/ai?tab=usage`) reads `/api/v1/usage/*` and
renders one report per selected period (24 hours, 7, 30 or 90 days). It has no
write path: prices and the ledger root come from `apps/api/config.yaml`, and
provider balances are read from the vendors named by the AI integrations. The
tab is mounted only when it is opened, so the supplier section never queries the
ledger; the retired `/usage` URL redirects here.

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
- Hard-deleting an account, or email-based reset. Deactivation is the supported
  removal path; a generated temporary password is shown once.
- Creating, editing or cancelling scheduled tasks. The assistant owns
  `tasks/tasks.json` through the chat tools, so the console only reads it; a
  second writer would also have to synchronise the scheduler's next-run state.
  The task table shows the last persisted result and the Run history card pages
  through `tasks/runs.jsonl` (newest first, filterable by task); the log remains
  the only store.
- Editing the AI provider registry outside the console. `data/ai.yaml` is
  written atomically and validated on load, but the console is the supported
  editor; a hand-edited file is only read.
- Entering vendor API keys in the browser. They stay in
  `packages/winnie-llm/.env` and are read only by `winnie-llm`; the console can
  only show whether the derived variable is set.
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
npm run check:i18n  # every static tr() key exists in zh-CN
npm run build       # key check + typecheck + Vite production build
```

Behavioral verification is recorded in
[docs/plan.md](../../docs/plan.md#6-verification-snapshot) and
[docs/decisions.md](../../docs/decisions.md).

## License

The console is part of Winnie and is licensed under the repository's
[Apache License 2.0](../../LICENSE).
