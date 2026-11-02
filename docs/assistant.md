# Assistant: an always-on personal assistant service

Status: milestone M6, first slice. Last updated: 2026-11-01.

This document is the design and delivery plan for the assistant capability:
chat, web search, conversation-managed scheduled tasks, and long-term memory
built from local files and served by the local document index.

## 1. Goal

Winnie should be reachable at any hour from a phone or a terminal, should
remember what was said without a database owning the truth, and should manage
its own schedule when asked in conversation.

Success for this slice means all of these are true:

1. A message from a phone browser gets an answer, and the answer may use fresh
   web results or the owner's local documents.
2. Every user, assistant and tool message is Markdown under
   `~/Documents/Mine/Winnie/conversations/`, readable and editable without the
   service.
3. Durable facts are distilled **from those files** into
   `~/Documents/Mine/Winnie/profile/*.md`, and a later answer can cite them by
   path and line.
4. A recurring task is created, listed and cancelled **by talking to the
   assistant**, persisted in `tasks/tasks.json`, and executed on schedule.
5. The owner can ask the assistant to inspect or change a local file and to run
   a local command; every such action is confined, audited, and confirmed before
   it runs.

Out of scope for this slice: voice input, a native mobile app, multi-tenant
isolation, autonomous long-running agents, embeddings, and an OS-level sandbox
for commands. Each is a named extension point rather than a hidden assumption.

## 2. Why files, and why the index owns retrieval

The project treats Markdown as the durable format for knowledge. The assistant
follows the same rule, with one deliberate split:

| Concern | Canonical store | Derived |
|---|---|---|
| What was said | one Markdown file per conversation | nothing |
| What is remembered | `profile/*.md`, one bullet per fact | the system-prompt digest |
| What runs on a schedule | `tasks/tasks.json` | next-run state in `.state/` |
| Run history | `tasks/runs.jsonl` | nothing |
| How recall works | — | the `winnie-index` catalog |

Full-text retrieval is **not** implemented inside the assistant. `winnie-index`
already tokenises mixed Chinese/English text, chunks by heading, keeps the
catalog in step with the filesystem and revalidates a document before returning
it. A second, in-process lexical index would duplicate that work and drift from
it, so the assistant queries the index service for documents and citations.

Memory has one deliberate exception: `MemoryStore.recall` scores the
already-parsed notes against the incoming message and puts the top few in the
prompt. It does not chunk, rank or cite documents — `path:line` still comes from
the index — and it exists because two failures are worse than a duplicate
matcher:

- a note is only visible if the model decides to search, and a paraphrase of the
  owner's wording does not have to trigger that decision;
- the index can be up to `scan.interval_seconds` behind a write.

Consequences that matter:

- The assistant can be rebuilt from the files; the index is rebuildable from
  the same files.
- The owner can edit or delete a memory by editing a file. The pinned digest
  obeys it on the next refresh, and the index obeys it on the next sweep or on
  the immediate reindex that a memory write triggers.
- Every answer can cite `path:line` because the index returns it, not because
  the assistant reconstructs it.

## 3. Architecture

```
 phone / curl
        │
        ▼
 FastAPI host ── assistant capability ────────────────┐
        │                                             │
        ▼                                             │
   AssistantService                                   │
        ├── Agent loop ── chat model (OpenAI-compatible)│
        │     ├── web_search       (Bing RSS)          │
        │     ├── fetch_page       (SSRF-guarded)      │
        │     ├── search_documents ──► winnie-index ───┤
        │     ├── read_document    ──► winnie-index    │
        │     ├── remember         (profile/*.md)      │
        │     └── schedule_task / list_tasks / cancel_task
        ├── ConversationStore  (append-only Markdown)  │
        ├── MemoryBuilder      (files -> facts)        │
        ├── AssistantScheduler (tasks.json -> run)     │
        └── MemoryStore        (profile files + digest)│
                                                       │
        ~/Documents/Mine/Winnie  ◄── indexed by ───────┘
```

The model/tool loop and the skill registry are implemented in
`winnie-chat`; local side effects are in `winnie-local`. The assistant imports
those shared layers and `winnie-core`; the host composes it. The index is a
separate loopback daemon (`packages/winnie-index`, `127.0.0.1:8790`) because the
same catalog also serves other agents through MCP.

## 4. Storage layout

Everything the assistant knows lives in one directory next to the owner's other
documents:

```
~/Documents/Mine/Winnie/
├── README.md                  # explains this directory; written once
├── profile/                   # durable facts, one file per category
│   ├── identity.md
│   ├── preferences.md
│   ├── people.md
│   ├── projects.md
│   └── ...
├── conversations/
│   └── 2026-11-01/
│       └── 20261101T021500Z-3f9a.md
├── tasks/
│   ├── tasks.json             # written by the assistant, not by hand
│   └── runs.jsonl
└── .state/                    # derived: memory watermark, next-run state,
                               # command/file audit and overwrite backups
```

The local file tools are separate from this storage: they operate under
`assistant.workspace.root` and always refuse the assistant's own
`conversations/`, `profile/`, `tasks/` and `.state/` directories. Commands run
in `assistant.exec.workdir`; their audit log and the file-tool backups live under
`.state/`.

The directory is created on startup with `README.md` and the subdirectories. It
sits inside the index root (`Documents/Mine`), so every Markdown file written
here is searchable: a memory write is reindexed immediately, and any other file
becomes searchable on the next sweep. `tasks.json`, `runs.jsonl` and `.state/`
are JSON and are not indexed.

The instance is **single-user** by design: `auth.allow_registration` is off, and
the bearer token protects the API rather than partitioning data. The owner id is
recorded in each conversation's front matter so a future multi-user layout can
migrate the files without guessing.

Because the directory is under `~/Documents`, it is likely inside a synced tree.
That is the requested location and it makes the files easy to back up; it also
means conversations leave the machine through whatever sync service watches
that tree. The README says so.

### 4.1 Conversation format

One file per conversation, named by UTC creation time and a random suffix so
lexical order is chronological order. The file is append-only after creation:

```markdown
---
id: 20261101T021500Z-3f9a
title: "明天的出行安排"
created_at: 2026-11-01T10:15:00+08:00
source: chat
channel: admin
owner: local
---

# 明天的出行安排

## user · 2026-11-01T10:15:03+08:00

明天上海会下雨吗？要不要带伞。

## tool:web_search · 2026-11-01T10:15:05+08:00

1. 上海天气预报 ...
   https://...

## assistant · 2026-11-01T10:15:09+08:00

明天上海有雨，建议带伞。
```

- The block header is `## <role> · <ISO timestamp>`; tool blocks are
  `## tool:<name> · <timestamp>`. Everything between headers is the message,
  verbatim.
- Tool blocks are part of the record: they explain which search result or local
  document an answer came from, and they are what the index chunks.
- `source` is `chat` or `task`; task runs also record `task: <id>`.
- `channel` records where the turn arrived: `admin` (the console), `web` (the
  phone page), `api` (a direct API client) or `task`. `source` says who started
  the turn, `channel` says where it came from; a future Telegram or ntfy
  adapter is a new channel, not a new source. A file written before channels
  existed falls back to `api`, or to `task` for a task-sourced file.
- The front matter is the only mutable part. Appends never rewrite earlier
  blocks.

### 4.2 Profile format

One Markdown file per memory category, one bullet per fact, with provenance in
a comment:

```markdown
---
category: preferences
pinned: true
updated: 2026-11-01
---

# Preferences

- [2026-11-01] 回答先给结论，再展开。 <!-- source: chat:20261101T021500Z-3f9a -->
```

- Four categories are always in the system prompt: `identity`, `preferences`,
  `procedures` and `constraints`, bounded by `memory.digest_chars`. The rest are
  reached through search and through the per-turn recall described below.
  `procedures` is pinned because a rule about *how* to answer is worthless if the
  only route to it is a search the model has to decide to run.
- The source comment makes a fact attributable and strippable: it is removed
  from the text shown to the model and returned by the memory API.
- A hand edit is legal: change the text or delete a line. Front matter can pin a
  normally-unpinned category (`pinned: true`), but it cannot unpin a pinned one —
  the category set is the contract, and a stale `pinned: false` in an older file
  would otherwise silently remove the rule from every prompt.

## 5. Long-term memory

### 5.1 Distillation

```
conversation files (messages after the watermark)
        │
        ▼
  LLM extraction ──► {"facts": [{"category", "text"}], "nothing_new": bool}
        │
        ▼
  dedupe (normalised text) ──► append to profile/<category>.md
        │
        ▼
  advance .state/memory.json
```

- The pipeline reads **files**, not the in-process message list. Memory is
  built from what was stored.
- A conversation is not distilled until it ends with an assistant message, so a
  half-written turn cannot become a fragment.
- The watermark advances only after writes succeed; duplicates are dropped by
  the normalised-text check, so a repeated run is a no-op.
- `nothing_new: true` advances the watermark and logs the result, so "nothing
  durable was said" is a recorded outcome rather than a silent gap.
- Facts are written in the language the user used, so the files stay readable
  for them; the extraction prompt itself is English.
- Distillation is a background task after each chat turn. It never blocks the
  reply, and a model failure leaves the watermark untouched for the next run.

### 5.2 Recall

`search_documents` calls the index, which returns ranked chunks with `path`,
`heading`, `start_line`/`end_line`, a snippet and the file's mtime.
`read_document` then reads a window through the index's `get`, which revalidates
the file against the catalog first, so the text is never stale even when the
sweep is behind.

Three read paths reach a turn, and they are not redundant:

- **Pinned profile facts** (`identity`, `preferences`, `procedures`,
  `constraints`) are read from disk every turn and are in the system prompt
  immediately.
- **Per-turn recall** scores the unpinned notes against the message *and the
  recent exchange* and puts at most six of them in the prompt with their
  `path:line`. A bigram shared with the message is worth three times one shared
  with the exchange, and characters count only in the message; the last
  `recall_context_messages` messages keep a subject alive across short turns, so
  "那明天呢？" still reaches the notes about tomorrow. It reads the parsed notes in process, so it sees a note written a
  second ago. `recall_limit: 0` turns it off; the pinned digest is unaffected.
- **Search** is the model's own call when it needs the wider corpus. A note
  written by `remember` is reindexed immediately through the daemon's
  `POST /admin/index`; everything else (a hand edit, a note from another
  writer) becomes searchable on the next sweep (`scan.interval_seconds`, 300 s
  by default). Recall covers the gap for notes.

A search that matches nothing strictly is retried by the daemon as an OR of the
query's tokens, and such hits are returned with `loose: true` so the tool can
say so. The strict behaviour is the default of the daemon API; the assistant
opts in, because it is the component that turns "nothing found" into a statement
to the owner. The tool text says explicitly that a miss is not proof a fact does
not exist, and the skill instructions forbid answering "it was never recorded"
from a single zero-result search.

The assistant does not filter search results to its own directory. The owner's
notes and the assistant's memory share one corpus, which is the point: a
question about a project can hit a note the owner wrote, and a question about
the assistant's memory hits `Winnie/`. The prompt tells the model that paths
under `Winnie/` are its own files, and every hit carries its path so the
distinction stays visible.

## 6. Chat engine, skills and tools

The model/tool loop does not live in the assistant capability. It lives in
[`packages/winnie-chat`](../packages/winnie-chat/README.md): the chat engine
selects the enabled skills, builds the system prompt from their instructions,
offers their tool schemas, runs the bounded loop and dispatches each tool call
to its owning skill. The assistant's `AssistantAgent` only creates the
conversation, records the user/tool/assistant messages and stores the reply.

One turn is:

1. The assistant appends the user message and gives `winnie-chat` the history,
   time, memory digest, the notes recalled for this message and any pending
   confirmation.
2. The chat engine asks the `SkillRegistry` for the skills enabled for this
   turn (and allowed in a scheduled run), then builds the system prompt and the
   tool schemas from those skills.
3. It calls the model, executes returned tool calls through the owning skill,
   and repeats while the round budget is not exhausted.
4. Each tool output is recorded as a `tool:` block; the final assistant text is
   appended and returned.

Skills are named bundles of tools, prompt instructions, permissions and policy.

| Skill | Tools | Where the work happens |
|---|---|---|
| `core` | — | Persona and general rules; always on, not toggleable |
| `web` | `web_search`, `fetch_page` | Bing RSS and the SSRF-guarded page fetcher |
| `weather` | `weather_forecast` | Open-Meteo forecast, rules and the daily weather watch |
| `gold` | `gold_price` | gold-api.com quote, move rules and the push watcher |
| `documents` | `search_documents`, `read_document` | the `winnie-index` daemon |
| `memory` | `remember` | `profile/*.md` |
| `tasks` | `schedule_task`, `list_tasks`, `cancel_task` | `tasks/tasks.json`; chat only |
| `local` | `list_files`, `read_file`, `write_file`, `run_command`, `confirm_action` | `winnie-local`, with review, whitelist, confirmation and audit; chat only |
| `phone` | `phone_status`, `phone_search`, `phone_calendar`, `phone_calls`, `phone_messages`, `phone_notifications`, `phone_location`, `phone_apps` | `winnie-phone` queries over the copy the owner's phone uploaded |
| `phone_write` | `phone_calendar_create`, `phone_calendar_update`, `phone_calendar_delete`, `phone_sms_send`, `phone_confirm`, `phone_cancel` | a `winnie-phone` command queue; the phone executes after a confirmation; chat only |
| `notify` | `notify_user` | the push queue; a scheduled task result is pushed automatically |
| `mail` | `search_mail`, `read_mail` | the cached mirror of the owner's mailbox in the application database; read-only |

Tools and their inputs:

| Skill | Tool | Input | Output |
|---|---|---|---|
| web | `web_search` | `query`, `limit?` | numbered title / URL / snippet list |
| web | `fetch_page` | `url` | readable text, truncated |
| weather | `weather_forecast` | `location?`, `latitude?`, `longitude?` | today/tomorrow forecast with umbrella, clothing, heat, wind and severe-weather advice |
| gold | `gold_price` | — | current gold price, window/day changes and the alert rules |
| documents | `search_documents` | `query`, `limit?` | `path:line-range`, heading, snippet, mtime |
| documents | `read_document` | `path`, `from_line?`, `lines?` | the current text with a line header |
| memory | `remember` | `category`, `text` | confirmation, or an error for an unknown category |
| tasks | `schedule_task` | `title`, `schedule`, `prompt`, `webhook?` | task id and next run |
| tasks | `list_tasks` | — | id, schedule, next run, last status |
| tasks | `cancel_task` | `id` | confirmation or "no such task" |
| local | `list_files` | `path?` | directory entries with sizes and `[protected]` / `[credential]` / `[outside]` markers |
| local | `read_file` | `path`, `start_line?`, `max_lines?` | text with its line range and sha256 |
| local | `write_file` | `path`, `content`, `mode?`, `expected_sha256?` | write summary, or a `PENDING` id |
| local | `run_command` | `argv`, `cwd?` | command output, or a `PENDING` id |
| local | `confirm_action` | `pending_id` | executes the pending write/command after a later confirmation |
| phone | `phone_status` | — | device, last sync, scopes, record counts, pending writes |
| phone | `phone_search` | `query`, `kinds?`, `days?`, `limit?` | cross-kind hits merged by time |
| phone | `phone_calendar` | `days_back?`, `days_ahead?`, `query?`, `limit?` | events with their ids and local times |
| phone | `phone_calls` | `days?`, `direction?`, `query?`, `limit?` | call-log lines |
| phone | `phone_messages` | `days?`, `address?`, `query?`, `unread_only?`, `limit?` | SMS lines with unread markers |
| phone | `phone_notifications` | `days?`, `app?`, `query?`, `limit?` | notification lines |
| phone | `phone_location` | `days?`, `limit?` | recent location fixes |
| phone | `phone_apps` | `query?`, `limit?` | installed apps with versions |
| phone_write | `phone_calendar_create` / `update` / `delete`, `phone_sms_send` | the write's fields | a `PENDING <id>` proposal, never a completed action |
| phone_write | `phone_confirm`, `phone_cancel` | `command_id` | `APPROVED` / `CANCELLED` and the next-sync caveat |
| notify | `notify_user` | `title`, `body?` | confirmation; the phone shows it as an alert |
| mail | `search_mail` | `query`, `folder?`, `unread_only?`, `limit?` | matching messages with their folder, sender, subject and the cache's age |
| mail | `read_mail` | `message_id` | one message's headers and body, fetched on first open and cached |

A scheduled task's result is pushed to the owner's phone automatically unless
`assistant.tasks.notify_results` is off, because a brief nobody sees is not a
brief. In conversation, `notify_user` is for a requested reminder or a result the
owner wanted to be told about, not for every reply. Push delivery, the cursor and
the Android foreground service are in [push.md](./push.md).

Mail is a **data source**, not a channel the model may use to write: `mail` has
`search_mail` and `read_mail` and nothing else, because a message body is written
by someone else and a tool that can reply turns "here is a request in an email"
into an action. The cache is filled by the mail capability's own sync (newest 50
per folder, headers only, body on first open), so every result states its age and
the instructions tell the model to say when that age matters. Times are rendered
in the owner's zone with an explicit offset. The skill is offered only when a
mailbox is configured, and it is scheduled-safe. Details in [mail.md](./mail.md).

Phone answers are snapshots. The prompt carries a compact digest of the last
sync time, the next 24 hours, unread SMS and today's missed calls, and every
phone tool result names the device and the times in the owner's zone. A write
is always two steps: the assistant proposes, reads the exact action back, and
approves only after an explicit yes in the next message. An approved write is
still pending until the phone syncs; the instructions forbid saying an SMS was
sent or an event created. Design and limits are in
[phone.md](./phone.md).

### Skill registry and admin

A `SkillManifest` declares the name, title, description, instructions, tools,
permissions, whether the skill is safe in scheduled task runs, and whether it is
toggleable. `SkillRegistry` owns the enable/disable state in
`skills/skills.json` under the assistant root, rejects duplicate tool names,
filters unavailable and scheduled-unsafe skills, and resolves a tool name to its
owner. Disabling a skill removes its tools from the schema list **and** its
instructions from the system prompt, so the model neither sees the tool nor is
told to use it.

The console's `/skills` page reads the registry through
`GET /api/v1/assistant/skills` and toggles a skill through
`PUT /api/v1/assistant/skills/{name}`. It shows title, description, tools,
permissions, availability (for example the index daemon being off), scheduled-run
safety and the full prompt instructions. The `core` skill is always on. A
disabled skill stays listed so the owner can see what is off.

Bounds and failure behaviour:

- `max_tool_rounds` defaults to 4. Exhausting it produces the last text or an
  explicit error, never an unbounded loop.
- Tool arguments are JSON; invalid JSON is an error string returned to the
  model, not a crash.
- Tool output is truncated to `max_tool_output_chars` before it enters the
  context and the conversation file.
- A model failure returns `502` and leaves the user message recorded, so a retry
  appends to the same conversation instead of losing it.
- A scheduled task run is given the tool set **without**
  `schedule_task`/`list_tasks`/`cancel_task` **and without the local tools**. A
  task cannot create tasks or run commands, which removes both the
  recursive-schedule and unattended-side-effect failure modes.
- `write_file` touches an existing file only after `read_file` returned its
  current sha256, and only in a later turn confirmed by the user.
- `run_command` and overwrites return a `PENDING` id and change nothing.
  `confirm_action` executes them only in a later turn whose user message is an
  explicit confirmation; a tool call twice in the same turn cannot execute.
- Every command and file write appends a JSONL audit line under `.state/`;
  overwrites and appends copy the old file to `.state/backups/` first.

The local tools are a deliberate, opt-in expansion. The execution itself lives
in [`packages/winnie-local`](../packages/winnie-local/README.md); the assistant
only builds an action and calls `LocalExecutor`, so no tool can quietly skip
planning, review, confirmation or audit.

`workspace` confines file paths to the configured roots and refuses the
assistant's own storage plus common credential directories; `.env`, private keys
and `.git` paths are refused even inside the root. Commands are **not** an OS
sandbox: they run as the API user, with a minimal environment that excludes
`WINNIE_*` and vendor keys, a timeout, an output cap and an audit line.
`exec.allow` is a guardrail, not a boundary.

Every action that is not on the whitelist is reviewed once by the bound
`assistant.chat` model before it can become a pending confirmation. The review sees the current user
message and the action (path, mode, preview/diff or argv/cwd), with common
secret patterns redacted, and must return
`{"allow": bool, "risk": "low|medium|high", "reason": "..."}`. A timeout,
provider error or malformed answer denies. The decision is appended to
`.state/review.jsonl`; the raw content is not. Whitelisted read-only operations
skip the review; they are configured under `assistant.local.whitelist`.

Search results, fetched pages, file contents and command output are framed as
untrusted data, because prompt injection is the obvious attack on a tool-using
assistant.

### Why not LangGraph yet

`DESIGN.md` names LangGraph for orchestration. This slice is a single-agent,
two-to-four-round tool loop with a skill registry; a graph framework would add a
dependency and hide the trace that is currently stored as conversation blocks.
The loop is behind `winnie_chat.ChatEngine`, so a LangGraph runner can replace
it when there are real branches (planner, subagents, retries by step). Moving
the loop out of the assistant is also what makes "chat" a module rather than a
side effect of the assistant's tool code.

## 7. Default watches: weather and gold

The weather watch is the first feature that is on by default. It has two halves,
and the `weather` skill owns both:

- `weather_forecast` lets the model answer "do I need an umbrella?" with the
  same numbers and advice the watcher uses.
- a deterministic daily check (07:00 by default) fetches the forecast and, only
  when the advice changed since the last check, records a notice and optionally
  POSTs a webhook.

No model call is involved in the watcher. A daily check costs one HTTP request
and cannot hallucinate a temperature.

### Rules

| Signal | Condition | Advice |
|---|---|---|
| rain | WMO rain code, probability at or above `rain_probability`, or at least 0.5 mm | take an umbrella |
| snow | snow code or any snowfall | slippery, keep warm |
| temperature drop | tomorrow's max is `temperature_delta` or more below today's | add clothes |
| temperature rise | tomorrow's max is `temperature_delta` or more above today's | adjust clothing |
| heat | today's max at or above `heat_threshold` | heat and hydration |
| cold | today's min at or below `cold_threshold` | keep warm |
| wind | either day's max wind at or above `wind_threshold` km/h | falling objects |
| severe | WMO 65/67/75/82/86/95–99 | stay indoors if possible, watch official alerts |

Advisories are composed into one short Chinese message with the two-day
forecast, the advice list and the data source. The language follows the
conversation, not the code.

### Delivery

- A notice is a conversation of its own: `source: weather`, `channel: weather`,
  and the first block is an `assistant` message. Nothing pretends the owner
  spoke, and the console's history can filter on the channel.
- The webhook body is
  `{"kind": "weather", "location", "title", "text", "conversation_id"}`.
- The fingerprint is the date plus the sorted advisory kinds. The same advice is
  not delivered twice on one day; a new kind (for example a wind warning that
  appears in the afternoon) is delivered even though the morning notice exists.
- `.state/weather.json` holds `next_run`, `last_run`, `last_status`, the
  fingerprint and the last conversation id, so a restart neither duplicates nor
  replays a notice.
- `POST /assistant/weather/check` runs the check for the console or a test. It
  records a notice when there is advice, but it does **not** move `next_run`;
  testing at 06:00 must not cancel that morning's scheduled run.

### Administration

The Skills page is the feature list. The weather row shows its title,
description, tool, `network` permission and availability; disabling it removes
the tool from the model and makes the watcher skip its next due check.

`weather_forecast` takes an optional `location` (or explicit coordinates) for the
call. Without one it uses the configured location. The parameter exists because
the memory rule "no location given → 武汉" could otherwise be displayed in the
prompt and then ignored: on 2026-11-01 the assistant read that rule, said so in
its reply, and still forecast 上海. A caller that passes coordinates should pass
`location` too, so the report is headed by a place name instead of raw numbers.

The scheduled check resolves its place the same way the chat does, because the
stored rule is a rule about the owner's questions, not about one code path:

- with `follow_phone: true` (the default) the watcher asks the phone for its most
  recent fix and uses those coordinates when the fix is newer than
  `phone_fix_max_age_minutes` (120 by default);
- otherwise — no phone, no recent fix, a lookup error, a `0,0` fix, or
  `follow_phone: false` — it uses the configured location;
- the notice title, the webhook body's `location` and `.state/weather.json`'s
  `last_location` all carry the place the forecast was actually for.

The age bound is the point: a phone that lay on a desk all night reports last
evening's city, and "another city, confidently" is worse than the configured
default. `GET /assistant/status` shows `location` (the configured place),
`follow_phone` and `last_location` (the place the last run used).

### Data source and limits

The forecast comes from [Open-Meteo](https://open-meteo.com): no key, global,
and one request for yesterday, today and tomorrow. The live check on this host
took 0.9 s for Shanghai and produced the rain and temperature-drop advisories.

Official Chinese warning feeds were tried and rejected: the keyless
`weather.com.cn` alarm endpoints answer `403`, and the commercial providers need
a key. The `severe` row is therefore derived from WMO codes and thresholds, and
the message says "留意官方预警" rather than claiming an official alert. A real
alert feed is a provider-port follow-up, not a rewrite: add an `alerts` source to
`WeatherService` and merge its items into the advisory list.

One deployment watches one location. Per-user or multi-city watches need a
settings surface the console does not have yet.

### Gold price watch

The `gold` skill answers "how much is gold?" and, more importantly, watches for a
sharp move and pushes an alert to the owner's app. It is deterministic like the
weather watcher: no model call, one quote every five minutes.

The source is [gold-api.com](https://gold-api.com) `XAU/USD`: no key, current
price only. The watcher keeps its own samples in `.state/gold.json` because the
feed has no candles, and it uses the quote's `updatedAt` as the sample time.

A move is 1 % or more against the sample closest to one hour ago, or 2.5 % over
24 hours; a two-hour cooldown follows any alert, and the message is labelled
with the move's actual age. The first possible alert needs about 30 minutes of
samples; a full hour window needs an hour of uptime. Samples persist across a
restart.

Delivery has two parts: a conversation (`source: gold`,
`channel: notification`) and a push through the same `Notifier` the
`notify_user` tool uses. The host resolves the owner — the only active account,
`cleanup` on this instance — so the model never handles a user id.
`POST /assistant/gold/check` samples and reports without pushing and without
moving the schedule; an inspection must not train the owner to ignore alerts.

Configuration, the exact rule table and the limits (USD per ounce only, no tick
feed, no market calendar) are in [gold.md](./gold.md).

## 8. Scheduled tasks, managed in conversation

There is no configuration file to edit. The user says what they want; the
assistant restates the schedule and the content, waits for an explicit yes, and
then calls `schedule_task`. That is the whole interface.

Example:

> **User:** 以后每天早上7点半给我一份简报，内容包括上海天气和我当天的待办。
>
> **Winnie:** 我先确认一下安排，你确认后我再创建：时间每天 07:30
> （Asia/Shanghai），内容……。确认没问题的话回我一句“可以”。
>
> **User:** 可以
>
> **Winnie:** 已创建。任务 id `task-905b11`，下次运行 2026-11-01 07:30。

Supported schedule forms, chosen so the model can produce them reliably and the
user can read them back:

| Form | Meaning |
|---|---|
| `every 15m` | every 15 minutes (also `h`, `d`; minimum 5m) |
| `daily 07:30` | every day at local time |
| `weekly mon 09:00` | every Monday at local time |

`tasks/tasks.json` is the program's file, not the user's:

```json
{
  "version": 1,
  "updated_at": "2026-11-01T03:09:04+08:00",
  "tasks": [
    {
      "id": "task-905b11",
      "title": "每日早间简报（上海天气+当天待办）",
      "schedule": "daily 07:30",
      "prompt": "生成今天的早间简报……",
      "enabled": true,
      "webhook": "",
      "created_at": "2026-11-01T03:09:04+08:00"
    }
  ]
}
```

- Ids are a slug plus a short random suffix, so a Chinese title still yields a
  usable id (`task-905b11`).
- The task's `prompt` is written by the model from the conversation and is what
  the task runs later. The user approved it in the confirmation step, which is
  why the confirmation is a hard prompt rule rather than a nicety.
- `next_run` is persisted in `.state/scheduler.json`, so a restart does not
  duplicate a run. A task with no state is scheduled forward from now.
- After downtime, a missed run fires once if it is within
  `tasks.catch_up_seconds` (default 3600) and is otherwise skipped to the next
  occurrence.
- Each run creates a conversation with `source: task` and appends one JSON line
  to `tasks/runs.jsonl` with status, duration, conversation id and error.
- Delivery is `store` always, plus an optional `webhook` POST
  (`{"task_id", "title", "text", "conversation_id"}`). A webhook failure is
  recorded; it does not lose the run.
- The loop ticks every `tasks.tick_seconds` in the API process. When the API is
  down, nothing runs; the run history is on disk so a gap is visible.
- The admin console reads the same definitions at `/tasks`: schedule, active or
  paused state, next and last run, last result, prompt and webhook target. It is
  read-only; creation and cancellation stay with the chat tools above. The same
  page lists every recorded execution from `GET /assistant/tasks/runs`, newest
  first, filterable by task and paged 25 at a time, with start time, duration,
  result, conversation id and both execution and delivery errors.

## 9. HTTP API

Mounted under `/api/v1`. Authentication reuses the bearer sessions; when
accounts are disabled the local owner is used.

| Method | Path | Purpose |
|---|---|---|
| POST | `/assistant/chat` | send `{message, conversation_id?, channel?}`, get the reply |
| POST | `/assistant/transcribe` | multipart `file` (audio), one recorded utterance → `{text}`; used by the phone's wake turn and the console's voice turn |
| GET | `/assistant/conversations` | recent conversations, newest first; `limit`, `offset`, `channel` |
| GET | `/assistant/conversations/{id}` | full transcript (tool blocks included) |
| GET | `/assistant/skills` | registered skills with enabled/available state, tools, permissions and instructions |
| PUT | `/assistant/skills/{name}` | enable or disable one skill; body `{enabled: bool}` |
| GET | `/assistant/memory` | profile files with entry counts and the digest |
| POST | `/assistant/memory/distill` | run the distillation now |
| GET | `/assistant/documents/search?q=` | index-backed search with provenance |
| GET | `/assistant/weather` | the weather watch's schedule and last result |
| POST | `/assistant/weather/check` | run the weather check now, without moving its schedule |
| GET | `/assistant/gold` | the gold watch's thresholds, schedule and last sample |
| POST | `/assistant/gold/check` | sample the gold price now and report, without pushing |
| GET | `/assistant/tasks` | tasks plus persisted next-run state |
| GET | `/assistant/tasks/runs` | recorded executions, newest first; `task_id`, `limit`, `offset` |
| POST | `/assistant/tasks/{id}/run` | run a task now, without moving its schedule |
| GET | `/assistant/ui` | mobile-friendly chat page |
| GET | `/assistant/status` | model, search, index, root, counts, scheduler state |

The chat response is non-streaming in this slice. Streaming is an additive
change behind the same route (SSE), and both chat surfaces are written so they
can switch without moving state.

The console chat at `/chat` and the phone page at `/assistant/ui` call the same
route. The console tags its turns `channel: admin`, the phone page `web`, a
direct caller defaults to `api`, and the scheduler writes `task`. The console's
history sidebar lists all four, filters by channel, and opens any transcript
with its tool blocks. A task-run conversation is read-only: the API rejects a
chat turn on it with `409`, because appending a human message to an unattended
record would mix two kinds of turn and would re-enable the task-management tools
under a scheduled prompt.

## 10. Configuration

Added to `apps/api/config.yaml`; defaults shown in `AssistantConfig`.

```yaml
assistant:
  enabled: true
  root: ~/Documents/Mine/Winnie
  timezone: Asia/Shanghai
  max_history_messages: 30
  max_tool_rounds: 4
  max_tool_output_chars: 4000
  search:
    provider: bing_rss       # bing_rss | fake
    endpoint: https://cn.bing.com/search
    max_results: 5
    timeout_seconds: 15
  index:
    enabled: true
    url: http://127.0.0.1:8790
    token_env: WINNIE_INDEX_TOKEN
    timeout_seconds: 5
    limit: 8
  memory:
    enabled: true
    digest_chars: 3000
    auto_distill: true
    max_facts_per_run: 12
    max_fact_chars: 500
    recall_limit: 6
    recall_chars: 1200
    recall_context_messages: 6
  tasks:
    enabled: true
    file: tasks/tasks.json
    tick_seconds: 30
    catch_up_seconds: 3600
    webhook_timeout_seconds: 10
  gold:
    enabled: true
    symbol: XAU
    schedule: every 5m
    window_minutes: 60
    threshold_percent: 1.0
    daily_threshold_percent: 2.5
    cooldown_minutes: 120
    history_hours: 48
    timeout_seconds: 15
    tick_seconds: 30
  weather:
    enabled: true
    location: 上海
    latitude: 31.2304
    longitude: 121.4737
    follow_phone: true
    phone_fix_max_age_minutes: 120
    schedule: daily 07:00
    webhook: ""
    rain_probability: 50
    temperature_delta: 5
    heat_threshold: 35
    cold_threshold: 0
    wind_threshold: 39
    timeout_seconds: 15
    webhook_timeout_seconds: 10
    tick_seconds: 30
  local:
    review:
      enabled: true
      timeout_seconds: 20
      max_action_chars: 4000
    whitelist:
      operations: ["list_files", "read_file"]
      commands: ["pwd", "ls", "cat", "head", "tail", "wc"]
      git_subcommands: ["status", "rev-parse", "ls-files"]
  workspace:
    enabled: true
    root: "~/Documents/Mine"
    extra_roots: ["~/Projects"]
    protected: []
    max_read_bytes: 200000
    max_write_bytes: 200000
    max_list_entries: 500
    confirm_writes: true
  exec:
    enabled: true
    workdir: "~/Documents/Mine"
    timeout_seconds: 30
    max_output_chars: 8000
    allow: []
    env: {}
    require_confirmation: true
    pending_ttl_seconds: 300
```

`workspace` and `exec` are opt-in. The file tools are confined to the roots and
refuse the assistant's own storage and credential paths. Commands run as the API
user, with a minimal environment (no `WINNIE_*` or vendor keys), a timeout and an
output cap; `allow: []` means any executable, and a non-empty list is a guardrail,
not a sandbox. `exec.pending_ttl_seconds` bounds how long a confirmation can wait.

`local.review` is the second gate: non-whitelisted actions are denied unless
the bound `assistant.chat` model returns an explicit JSON allow.
`local.whitelist` is the
explicit bypass for read-only operations. The default git whitelist is
`status`, `rev-parse` and `ls-files`; `diff`, `log` and `show` can run
repository-configured textconv/external-diff helpers, so they are reviewed by
default. Adding a write command or a shell here removes its review; the human
confirmation (if enabled) still applies.

Skills are not configuration. Their enable/disable state is
`skills/skills.json` under the assistant root, written by the console; the
services behind a skill (index, local execution, tasks, memory) are still
configured by the blocks above. A skill whose service is off is shown as
unavailable in the console and is not offered to the model; the core skill has
no service and is always on.

`assistant.index` is the capability's view of the daemon. The host also has a
top-level `index` block for the console proxy; they are deliberately separate so
hiding the console page does not break the assistant's recall.

Secrets stay in the environment: the variable named by the bound `assistant.chat`
integration for the model and, if the daemon sets one, `WINNIE_INDEX_TOKEN` for
the index. A missing model key leaves the rest of the API up; a chat turn returns
`502` naming the variable, rather than silently answering from the fake model.
The provider and model are selected on the AI suppliers page; a fresh registry
leaves `assistant.chat` unbound until a supplier model is configured. See
[ai-providers.md](./ai-providers.md).

## 11. Security and privacy

- **Local files and commands are opt-in.** Conversations, profile notes and run
  history are under `assistant.root`. The file tools are confined to
  `assistant.workspace.root`/`extra_roots` and refuse the assistant's own storage
  and common credential paths. Commands run as the API user with a minimal
  environment and are **not sandboxed**. Both are audited under `.state/`, and
  overwrites/commands require a later explicit confirmation. Enable them only
  with `auth.enabled: true` and on a trusted network.
- **Auth.** When accounts are enabled every assistant route requires a bearer
  token. The instance is single-user, so the token is access control, not a
  tenancy boundary. The UI stores it in `localStorage` and sends it as a header.
- **Index boundary.** The index binds loopback only. Its `/search` and `/doc`
  endpoints are read-only. The one write the assistant makes is
  `POST /admin/index` with a single absolute path under a configured root, used
  to publish a memory note immediately after writing it; the daemon still
  decides what is indexable and refuses anything outside its roots or above
  `index.max_file_mb`.
- **SSRF.** `fetch_page` accepts only `http`/`https`, resolves the host and
  rejects loopback, private, link-local and reserved addresses; redirects are
  followed manually with the same check per hop.
- **Prompt injection.** Search results, fetched pages, local documents, file
  contents and command output are untrusted data. Local side effects exist only
  behind the local tools: paths are confined and audited, commands are audited
  and rate-bounded by a timeout, and overwrites/commands require a human
  confirmation in a later turn. The model cannot execute by calling the tool
  twice in one turn.
- **Confirmation is a turn boundary.** A pending action is tied to the
  conversation and to the turn that created it. It runs only when the next user
  message is an explicit, short confirmation; a new non-confirmation message
  discards it, and it expires after `pending_ttl_seconds`.
- **Every non-whitelisted action gets an LLM review.** The review is a separate
  model call with a dedicated security prompt, not the assistant's own
  judgement. It sees the current user message and the action, with common
  secret patterns redacted. Timeouts, provider errors and malformed JSON deny.
  The decision and reason go to `.state/review.jsonl`; file contents and command
  output do not.
- **The whitelist is explicit and read-only by default.** `list_files`,
  `read_file`, `pwd`, `ls`, `cat`, `head`, `tail`, `wc`, and the git
  `status`/`rev-parse`/`ls-files` subcommands skip the review. The policy still
  checks their path arguments against the workspace and refuses credential
  paths. Adding a write command or a shell to `local.whitelist.commands` makes
  it auto-approved; that is the owner's explicit risk.
- **Review data leaves the process.** The review sends the user's current
  message and a bounded, redacted action summary/preview to the model provider.
  Do not use a remote review model for files that must never leave the machine.
- **Skill toggles are an admin control.** Disabling a skill removes its tools
  and prompt instructions from the next turn; it is not a security boundary
  against a request that the model would otherwise make with another skill. The
  local skill still delegates to `winnie-local`, so disabling it is not required
  for the review/whitelist/confirmation controls to apply. The core skill cannot
  be disabled, and the admin routes use the same bearer session as the console.
- **Skills are code-registered today.** There is no arbitrary plugin loading,
  no user-supplied tool code, and no file-based skill discovery in this slice;
  the registry is a fixed set composed by the assistant host.
- **Audit and backups.** `files.jsonl` and `exec.jsonl` under `.state/` record
  the path/argv, exit code, duration and hashes, not file contents or command
  output. Overwrites and appends copy the old file to `.state/backups/` first.
- **Commands are not a sandbox.** A command can read anything the API user can
  read and reach the network. The allowlist checks the executable name only;
  `bash`/`python`/`node` restore full shell power. A real sandbox (bubblewrap,
  firejail or a container) is a follow-up, not a claim.
- **Search terms.** The default web provider is Bing's RSS output, intended for
  personal, non-commercial aggregation. A paid search API can replace it
  through the `SearchProvider` port.
- **Weather data.** The weather watch sends only the configured coordinates and
  timezone to Open-Meteo; it sends no conversation text, file content or
  identity.
- **Market data.** The gold watch sends only the symbol to gold-api.com; it
  sends no conversation text, identity or holdings. A push body is a price and
  a percentage, short enough for a lock screen. A weather notice is stored like any other conversation and can be
  indexed by the local index.
- **Sync.** The storage directory is under `~/Documents`; if that tree is
  synced, conversations leave the machine through the sync service. This is a
  consequence of the requested location, stated in the directory's README.

## 12. Delivery plan

| Step | Deliverable | Exit criteria | Status |
|---|---|---|---|
| A1 | Conversation and profile files, distillation | A chat turn lands in a file; distillation writes a fact | Done |
| A2 | Agent loop, web tools, index-backed recall | A real model answers with a web result and cites a local path | Done |
| A3 | Conversation-managed tasks | A task is created by chat, persisted, run and cancelled | Done |
| A4 | Chat page, docs, verification | Phone-sized page sends and renders; suite passes | Done |
| A5 | Local file tools, LLM review and confirmed commands | List/read/write under the workspace; non-whitelisted actions pass an LLM review and a later confirmation; audit, review and protected paths | Done |
| A6 | Dedicated chat engine and skill registry | `winnie-chat` owns the loop/prompt/skills; the assistant only persists; admin lists, inspects and toggles skills | Done |
| A7 | Default weather watch | Daily forecast check writes an umbrella/clothing notice only when advice changed; the skill exposes the same forecast to chat; admin can switch it off | Done |
| A8 | Gold price watch with app push | Samples every 5 minutes; a ≥1% hourly or ≥2.5% daily move records a notice and pushes the Winnie app; the skill exposes the quote to chat; admin can switch it off | Done |
| A9 | The memory read path | A fact written in one turn is in the next turn's prompt (pinned digest or recall); a paraphrase finds the note; the weather rule reaches the tool as a per-call location | Done |

Follow-ups after this slice, in order:

1. Streaming replies over SSE.
2. Vector retrieval once an embedding provider is configured; the index would
   grow a dense tier, not the assistant.
3. Delivery adapters beyond webhook (ntfy/Telegram) behind one port.
4. A worker outside the API process so tasks run while the API is down.
5. Fewer redundant tool calls: the live recall test used five
   `search_documents` calls for one question, which is accurate but wasteful.
6. Sandbox commands with bubblewrap/firejail or a container, and decide
   whether scheduled tasks may ever use local tools (today they cannot).

## 13. Verification

Automated:

```bash
cd apps/api && .venv/bin/python -m pytest -q
```

Covers the conversation store (including channel recording, filtering and the
task-run read-only guard), profile files and digest, distillation idempotency,
the index client, the tool loop with a fake model, task creation and
cancellation, scheduler arithmetic, the HTTP routes, and the local tools:
path confinement, protected/credential paths, overwrite hashes, backups,
command timeout/allowlist/env scrubbing, the confirmation turn boundary, the
audit logs and task-run exclusion.

Live, on 2026-11-01, with DeepSeek, Bing RSS, `winnie-index` on `127.0.0.1:8790`
and the real storage root:

| Check | Observed |
|---|---|
| `remember` via chat | wrote `profile/preferences.md` with `source: chat:<id>` and the fact in Chinese |
| Ask for a daily task | the model restated the schedule and waited; after "可以" it called `schedule_task` |
| Task persistence | `tasks/tasks.json` held the task with a model-written prompt; `next_run` was 07:30 |
| Index build | `added: 6`, `chunks: 17` for the new `Winnie/` files |
| Recall | the reply cited `Winnie/profile/preferences.md:7` after `search_documents` + `read_document` |
| Task run | manual run finished in 6.7 s, status `ok`, task-sourced conversation with tool blocks, one `runs.jsonl` line |
| Restart | task, next run, conversations and profile entries survived |
| Console chat | Firefox against a disposable API and assistant root: login, new chat, four-channel history, channel filter, task record read-only, tool trace, light/dark and mobile with no horizontal overflow |
| Local tools | Disposable service with a scripted model: `read_file`/`write_file` worked; a command stayed pending until the next "确认" turn; the file and command audit lines were written |
| Local review | Scripted reviewer in the test suite | Whitelisted reads skipped the reviewer; a non-whitelisted write/command was allowed or denied by the reviewer; a denied action changed nothing; malformed JSON, timeout and review errors denied |
| Chat engine and skills | `winnie-chat` and API tests | Registry persistence/duplicates/filtering; enabled-only schemas and instructions; disabled-tool rejection; skills list/toggle/persistence |
| Admin skills page | Firefox production preview | Skills listed with tools/permissions/instructions; unavailable document index shown; core always on; web toggled off and on with persistence; light/dark/mobile with no horizontal overflow |
| Weather check | `POST /assistant/weather/check` against Open-Meteo | 0.9 s; advisories `rain` and `temperature_drop`; notice message asked for an umbrella and an extra layer; conversation `source: weather`, `channel: weather`, assistant-only first block; `next_run` unchanged |
| Weather in chat | DeepSeek turn asking about tomorrow | called `weather_forecast`; reply named the 86% probability, 13.4–20 °C, the 6.6 °C drop and Open-Meteo as the source |
| Weather feature toggle | `PUT /assistant/skills/weather` | removed and restored the tool; state persisted in `skills/skills.json` |
| Gold price check | `POST /assistant/gold/check` against gold-api.com | 0.75 s; XAU/USD 3,876.40; rules and the first-run "history insufficient" message |
| Gold in chat | DeepSeek turn asking about the price | called `gold_price`; quoted 3,876.40 and refused to invent a 24-hour change |
| Gold push wiring | `GET /assistant/gold` after startup | `push: true`; the same `Notifier` used by `notify_user`, which the app's push test already reached for `cleanup` |

The local-tools check used a scripted model rather than a vendor model: the
confirmation gate is in the tool layer, so the provider does not change the
result.

## 14. Open questions

- Which push channel is the real one: ntfy, Telegram, or a self-hosted webhook?
  The tool accepts one URL; the default is unset.
- Should the assistant's files be excluded from the general document search to
  reduce noise, or is one corpus the point? Today it is one corpus; the paths
  make the distinction visible.
- How should a task be edited later: cancel and recreate (today), or an
  `update_task` tool with the same confirmation step?
- Should commands run in an OS sandbox (bubblewrap, firejail or a container)?
  Today they run as the API user with an LLM review, a confirmation gate and an
  audit log.
- Should the reviewer be a separate, smaller model from the bound
  `assistant.chat` model? Today it reuses the assistant model; a separate model
  would separate the proposer and the reviewer.
- Which read-only commands belong on the default whitelist? The current batch is
  deliberately narrow; `git diff`/`log`/`show` are reviewed because a
  repository can configure helpers that execute.
- Should the weather location and schedule be editable in the console instead of
  `config.yaml`? Today the console toggles the feature; a city change is a
  deployment change. Multi-location is the same question.
- Should a weather notice respect quiet hours or a separate notification
  channel? The default 07:00 check is meant to be the morning brief; a late
  severe-weather update is currently only sent if a new advisory kind appears.
- Should the gold thresholds be per-user or editable in the console? Today they
  are deployment settings shared by the instance; a per-user threshold needs a
  settings surface the console does not have.
- Is USD per troy ounce enough, or should the alert also show CNY per gram? The
  latter needs an FX source and a unit conversion.
- Should skills be loadable from `SKILL.md` files, or stay as code-registered
  manifests until there is a second consumer? Today they are registered by the
  assistant host; the admin only toggles them.
- Should the default workspace add `~/Downloads` or `~/Desktop`? Today it is
  `~/Documents/Mine` plus `~/Projects`; the config is the boundary.
- Should scheduled task runs ever get file or command tools? Today they do not,
  because there is no human turn to confirm.
- The live recall test needed several searches because the first queries were
  broad. Better tool descriptions or a two-step "find then read" prompt may cut
  that cost.
