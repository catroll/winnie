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

Out of scope for this slice: voice input, a native mobile app, multi-tenant
isolation, autonomous long-running agents, and embeddings. Each is a named
extension point rather than a hidden assumption.

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

Retrieval is **not** implemented inside the assistant. `winnie-index` already
tokenises mixed Chinese/English text, chunks by heading, keeps the catalog in
step with the filesystem and revalidates a document before returning it. A
second, in-process lexical index would duplicate that work and drift from it,
so the assistant queries the index service and only keeps the small pinned
digest in process.

Consequences that matter:

- The assistant can be rebuilt from the files; the index is rebuildable from
  the same files.
- The owner can edit or delete a memory by editing a file. The next index sweep
  obeys it, and the pinned digest obeys it immediately.
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

The capability imports only `winnie_core`; the host composes it. The index is a
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
└── .state/                    # derived: memory watermark, next-run state
```

The directory is created on startup with `README.md` and the subdirectories. It
sits inside the index root (`Documents/Mine`), so every Markdown file written
here becomes searchable on the next sweep; `tasks.json`, `runs.jsonl` and
`.state/` are JSON and are not indexed.

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

- `pinned: true` files are always included in the system prompt, bounded by
  `memory.digest_chars`. Everything else is reached through the index.
- The source comment makes a fact attributable and strippable: it is removed
  from the text shown to the model and returned by the memory API.
- A hand edit is legal: change the text, delete a line, or set `pinned` to
  `false`. The pinned digest obeys it on the next refresh, and the index obeys
  it on its next sweep.

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

Freshness is split on purpose:

- **Pinned profile facts** are read from disk every turn and are in the system
  prompt immediately.
- **Everything else** becomes searchable on the index's next sweep
  (`scan.interval_seconds`, 300 s by default) or on an explicit build. A fact
  written seconds ago may therefore not be searchable yet; the conversation it
  came from is still in the model's short-term history.

The assistant does not filter search results to its own directory. The owner's
notes and the assistant's memory share one corpus, which is the point: a
question about a project can hit a note the owner wrote, and a question about
the assistant's memory hits `Winnie/`. The prompt tells the model that paths
under `Winnie/` are its own files, and every hit carries its path so the
distinction stays visible.

## 6. Agent loop and tools

One turn is:

1. Append the user message to the conversation file.
2. Build the message list: system prompt (persona, current time, pinned memory
   digest) plus the last `max_history_messages` user/assistant messages from the
   file.
3. Call the chat model with the tool schemas.
4. While the model returns tool calls and the round budget is not exhausted:
   execute the tools, append their outputs as tool blocks and as `tool`
   messages, call the model again.
5. Append the final assistant text and return it.

Tools:

| Tool | Input | Output |
|---|---|---|
| `web_search` | `query`, `limit?` | numbered title / URL / snippet list |
| `fetch_page` | `url` | readable text, truncated |
| `search_documents` | `query`, `limit?` | `path:line-range`, heading, snippet, mtime |
| `read_document` | `path`, `from_line?`, `lines?` | the current text with a line header |
| `remember` | `category`, `text` | confirmation, or an error for an unknown category |
| `schedule_task` | `title`, `schedule`, `prompt`, `webhook?` | task id and next run |
| `list_tasks` | — | id, schedule, next run, last status |
| `cancel_task` | `id` | confirmation or "no such task" |

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
  `schedule_task`/`list_tasks`/`cancel_task`. A task cannot create tasks, which
  removes the recursive-schedule failure mode instead of asking the model to
  behave.

There is no shell, no file write outside `profile/`, and no credential-bearing
tool. Search results, fetched pages and local documents are framed in the system
prompt as untrusted data, because prompt injection is the obvious attack on a
tool-using assistant.

### Why not LangGraph yet

`DESIGN.md` names LangGraph for orchestration. This slice is a single-agent,
two-to-four-round tool loop with a fixed tool set; a graph framework would add a
dependency and hide the trace that is currently stored as conversation blocks.
The agent is behind an `AssistantAgent` seam, so a LangGraph runner can replace
the loop when there are real branches (planner, subagents, retries by step).

## 7. Scheduled tasks, managed in conversation

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

## 8. HTTP API

Mounted under `/api/v1`. Authentication reuses the bearer sessions; when
accounts are disabled the local owner is used.

| Method | Path | Purpose |
|---|---|---|
| POST | `/assistant/chat` | send `{message, conversation_id?}`, get the reply |
| GET | `/assistant/conversations` | recent conversations, newest first |
| GET | `/assistant/conversations/{id}` | full transcript (tool blocks included) |
| GET | `/assistant/memory` | profile files with entry counts and the digest |
| POST | `/assistant/memory/distill` | run the distillation now |
| GET | `/assistant/documents/search?q=` | index-backed search with provenance |
| GET | `/assistant/tasks` | tasks plus persisted next-run state |
| GET | `/assistant/tasks/runs` | recorded executions, newest first; `task_id`, `limit`, `offset` |
| POST | `/assistant/tasks/{id}/run` | run a task now, without moving its schedule |
| GET | `/assistant/ui` | mobile-friendly chat page |
| GET | `/assistant/status` | model, search, index, root, counts, scheduler state |

The chat response is non-streaming in this slice. Streaming is an additive
change behind the same route (SSE), and the page is written so it can switch
without moving state.

## 9. Configuration

Added to `apps/api/config.yaml`; defaults shown in `AssistantConfig`.

```yaml
assistant:
  enabled: true
  root: ~/Documents/Mine/Winnie
  timezone: Asia/Shanghai
  # Empty means: reuse llm.base_url, llm.model and llm.api_key_env.
  model: fake
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
    digest_chars: 2000
    auto_distill: true
    max_facts_per_run: 12
    max_fact_chars: 500
  tasks:
    enabled: true
    file: tasks/tasks.json
    tick_seconds: 30
    catch_up_seconds: 3600
    webhook_timeout_seconds: 10
```

`assistant.index` is the capability's view of the daemon. The host also has a
top-level `index` block for the console proxy; they are deliberately separate so
hiding the console page does not break the assistant's recall.

Secrets stay in the environment: `WINNIE_LLM_API_KEY` for the model and, if the
daemon sets one, `WINNIE_INDEX_TOKEN` for the index. A missing model key leaves
the rest of the API up and returns `503` naming the variable, rather than
silently answering from the fake model.

## 10. Security and privacy

- **Local files only.** Conversations, profile notes and run history are under
  `assistant.root`. Nothing is sent anywhere except the configured model, the
  web search endpoint and the loopback index.
- **Auth.** When accounts are enabled every assistant route requires a bearer
  token. The instance is single-user, so the token is access control, not a
  tenancy boundary. The UI stores it in `localStorage` and sends it as a header.
- **Index boundary.** The index binds loopback only. Its `/search` and `/doc`
  endpoints are read-only; the assistant never writes through it.
- **SSRF.** `fetch_page` accepts only `http`/`https`, resolves the host and
  rejects loopback, private, link-local and reserved addresses; redirects are
  followed manually with the same check per hop.
- **Prompt injection.** Search results, fetched pages and local documents are
  untrusted data. Tools have no side effects outside `profile/` and
  `tasks/tasks.json`, and every write is attributed so a poisoned fact can be
  traced and deleted.
- **Search terms.** The default web provider is Bing's RSS output, intended for
  personal, non-commercial aggregation. A paid search API can replace it
  through the `SearchProvider` port.
- **Sync.** The storage directory is under `~/Documents`; if that tree is
  synced, conversations leave the machine through the sync service. This is a
  consequence of the requested location, stated in the directory's README.

## 11. Delivery plan

| Step | Deliverable | Exit criteria | Status |
|---|---|---|---|
| A1 | Conversation and profile files, distillation | A chat turn lands in a file; distillation writes a fact | Done |
| A2 | Agent loop, web tools, index-backed recall | A real model answers with a web result and cites a local path | Done |
| A3 | Conversation-managed tasks | A task is created by chat, persisted, run and cancelled | Done |
| A4 | Chat page, docs, verification | Phone-sized page sends and renders; suite passes | Done |

Follow-ups after this slice, in order:

1. Streaming replies over SSE.
2. Vector retrieval once an embedding provider is configured; the index would
   grow a dense tier, not the assistant.
3. Delivery adapters beyond webhook (ntfy/Telegram) behind one port.
4. A worker outside the API process so tasks run while the API is down.
5. Fewer redundant tool calls: the live recall test used five
   `search_documents` calls for one question, which is accurate but wasteful.

## 12. Verification

Automated:

```bash
cd apps/api && .venv/bin/python -m pytest -q    # 128 passed
```

Covers the conversation store, profile files and digest, distillation
idempotency, the index client, the tool loop with a fake model, task creation
and cancellation, scheduler arithmetic and the HTTP routes.

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

## 13. Open questions

- Which push channel is the real one: ntfy, Telegram, or a self-hosted webhook?
  The tool accepts one URL; the default is unset.
- Should the assistant's files be excluded from the general document search to
  reduce noise, or is one corpus the point? Today it is one corpus; the paths
  make the distinction visible.
- How should a task be edited later: cancel and recreate (today), or an
  `update_task` tool with the same confirmation step?
- The live recall test needed several searches because the first queries were
  broad. Better tool descriptions or a two-step "find then read" prompt may cut
  that cost.
