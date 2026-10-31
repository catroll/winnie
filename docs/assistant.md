# Assistant: an always-on personal assistant service

Status: milestone M6, first vertical slice. Last updated: 2026-11-01.

This document is both the design and the delivery plan for the assistant
capability: chat, web search, scheduled tasks and long-term memory built from
locally stored conversations.

## 1. Goal

Winnie should be reachable at any hour from a phone or a terminal, and should
remember what was said without a database owning the truth.

Success for this milestone means all four of these are true at once:

1. A message sent from a phone browser gets an answer, and the answer may use
   fresh web results.
2. Every user and assistant message is a Markdown file under
   `data/assistant/conversations/`, readable and editable without the service.
3. Durable facts are distilled **from those files** into
   `data/assistant/memory/*.md`, and a later answer can cite them.
4. A task declared in `tasks.yaml` runs on its schedule, writes a conversation,
   and is delivered to a webhook when one is configured.

Out of scope for this slice: voice input, a native mobile app, multi-tenant
isolation, autonomous long-running agents, and vector retrieval. Each is a
named extension point rather than a hidden assumption.

## 2. Why files are the source of truth

The project already treats Markdown as the durable format for knowledge
(`DESIGN.md`, the meeting note sink). The assistant follows the same rule:

| Concern | Canonical store | Derived |
|---|---|---|
| What was said | one Markdown file per conversation | nothing |
| What is remembered | one Markdown file per memory category | the in-process index |
| What runs on a schedule | `tasks.yaml` | next-run state in `.state/` |
| Delivery history | `tasks/runs.jsonl` | nothing |

Consequences that matter:

- The service can be rebuilt from the files. A corrupt or deleted index is a
  non-event; a corrupt conversation file is visible and repairable by hand.
- The user can edit, correct or delete a memory by editing a file, and the next
  index refresh obeys it. The automatic path never gets the last word.
- Backups are `rsync`, not a database dump.

## 3. Architecture

```
 phone / curl / webhook
        │
        ▼
 FastAPI host ── assistant capability ───────────────┐
        │                                             │
        ▼                                             │
   AssistantService                                  │
        ├── Agent loop ── chat model (OpenAI-compatible)│
        │     ├── web_search  (Bing RSS, provider port)│
        │     ├── fetch_page  (SSRF-guarded HTTP GET)  │
        │     ├── memory_search                         │
        │     └── memory_remember                       │
        ├── ConversationStore  (append-only Markdown)  │
        ├── MemoryBuilder      (files -> facts)        │
        ├── MemoryIndex        (lexical, rebuildable)  │
        └── Scheduler          (tasks.yaml -> run)     │
```

The capability imports only `winnie_core`; the host composes it. This is the
same layering rule as meetings and the gateway.

## 4. Conversation storage

```
data/assistant/
├── conversations/
│   └── 2026-11-01/
│       └── 20261101T021500Z-3f9a.md
├── memory/
│   ├── identity.md
│   ├── preferences.md
│   ├── people.md
│   ├── projects.md
│   └── ...
├── tasks.yaml
├── tasks/
│   └── runs.jsonl
└── .state/
    ├── memory.json
    └── scheduler.json
```

One file per conversation, named by its UTC creation time and a random suffix so
lexical order is chronological order. The instance is **single-user** by design:
`auth.allow_registration` is off, all conversations belong to the owner of the
deployment, and the bearer session protects the API rather than partitioning
data. The owner id is recorded in the front matter so a future multi-user
layout can migrate the files without guessing.

The file is append-only after creation:

```markdown
---
id: 20261101T021500Z-3f9a
title: "明天的出行安排"
created_at: 2026-11-01T10:15:00+08:00
source: chat
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
- A tool block records what the model saw. It is part of the communication
  record because it explains the answer, but chat rendering skips it.
- `source` is `chat`, `task` or `api`. Task runs also record `task: <id>`.
- The front matter is the only mutable part (title set after the first user
  message). Appends never rewrite earlier blocks.

The store exposes list/get/append/create. A per-conversation `asyncio.Lock`
serialises appends inside the process, which is sufficient because the
scheduler runs in the same process as the API.

## 5. Long-term memory

### 5.1 Memory files

One Markdown file per category from `DESIGN.md` (identity, preferences, people,
projects, domain, procedures, constraints, resources, episodes). Every entry is
one list item with visible provenance:

```markdown
---
category: preferences
pinned: true
updated: 2026-11-01
---

# Preferences

- [2026-11-01] 回答先给结论，再展开。 <!-- source: conversation:20261101T021500Z-3f9a -->
```

- `pinned: true` files are always included in the system prompt (bounded by
  `memory.digest_chars`). Everything else is retrieved on demand.
- The `source:` comment makes an entry attributable. It is stripped from the
  text that is indexed or shown to the model.
- Edits by hand are legal: change the text, delete the line, or add prose. The
  index rebuilds from the files.

### 5.2 Distillation pipeline

```
conversation files (new messages since the watermark)
        │
        ▼
  LLM extraction prompt  ──►  {"facts": [{"category", "text"}], "nothing_new": bool}
        │
        ▼
  dedupe (normalised text per file)  ──►  append to memory/<category>.md
        │
        ▼
  advance the watermark in .state/memory.json
```

- The pipeline reads **files**, not the in-process message list. That is the
  literal requirement: memory is built from what was stored.
- `.state/memory.json` maps conversation id to the number of messages already
  processed. A run is idempotent: the watermark advances only after the writes
  succeed, and duplicate facts are dropped by the normalised-text check.
- Facts are written in the language the user used, so the memory file stays
  readable for them; the extraction prompt itself is English.
- `nothing_new: true` advances the watermark and logs the result, so "nothing
  durable was said" is a recorded outcome rather than a silent gap.
- Model failure leaves the watermark untouched and the next run retries. It
  never blocks the chat response: distillation is a background task.
- Facts are capped per run (`memory.max_facts_per_run`) and each fact's length
  is capped, so a runaway extraction cannot rewrite the memory in one pass.

### 5.3 Retrieval

The index is rebuilt at startup and after every write, from the files. Scoring
is lexical: ASCII word tokens plus CJK character bigrams, with an inverse
document frequency weight and a bonus for title/category hits. This is enough
for a personal corpus of thousands of entries and needs no embedding service,
which the current deployment does not have.

The alternative — Qdrant plus an embedding model — is the designed end state
(`DESIGN.md`) and stays a port away: `MemoryIndex.search` is the only entry
point the agent uses. The reason it is not first is written down in
`docs/decisions.md`: no embedding provider is configured here, and a lexical
index built from files is verifiable today.

Retrieval is explainable: every hit carries its file path and line, and
`memory_search` returns them to the model and to the user.

This index is not the source-document index: memory holds distilled facts about
the owner, while documents outside the repository are the subject of the
[local document index plan](./knowledge-index.md).

## 6. Agent loop and tools

One turn is:

1. Append the user message to the conversation file.
2. Build the message list: system prompt (persona, current time, memory digest)
   plus the last `max_history_messages` user/assistant messages from the file.
3. Call the chat model with the tool schemas.
4. While the model returns tool calls and the round budget is not exhausted:
   execute the tools, append their outputs as tool blocks and as `tool`
   messages, call the model again.
5. Append the final assistant text and return it.

Bounds and failure behaviour:

- `max_tool_rounds` defaults to 4. Exhausting it produces the last text or an
  explicit error, never an unbounded loop.
- Tool arguments are JSON; invalid JSON is an error string returned to the
  model, not a crash.
- Tool output is truncated to `max_tool_output_chars` before it enters the
  context and the conversation file.
- A model or transport failure returns `502` and leaves the user message
  recorded, so a retry appends to the same conversation instead of losing it.

Tools exposed to the model:

| Tool | Input | Output |
|---|---|---|
| `web_search` | `query`, `limit?` | numbered title / URL / snippet list |
| `fetch_page` | `url` | readable text, truncated |
| `memory_search` | `query`, `limit?` | entries with `path:line` provenance |
| `memory_remember` | `category`, `text` | confirmation, or an error for an unknown category |

There is no shell, no file write outside the memory directory, and no
credential-bearing tool. Web content is returned as text and framed in the
system prompt as untrusted data, because prompt injection is the obvious attack
on a tool-using assistant.

### Why not LangGraph yet

`DESIGN.md` names LangGraph for orchestration. This slice is a single-agent,
two-to-four-round tool loop with a fixed tool set; a graph framework would add a
dependency and hide the trace that is currently stored as conversation blocks.
The agent is behind an `AssistantAgent` seam, so a LangGraph runner can replace
the loop when there are real branches (planner, subagents, retries by step).
This deferral is recorded in `docs/decisions.md` with that exit condition.

## 7. Scheduled tasks

Tasks are declared in `data/assistant/tasks.yaml`, which the user edits:

```yaml
timezone: Asia/Shanghai

tasks:
  - id: morning-brief
    title: Morning brief
    schedule: daily 07:30
    prompt: >
      Summarise today's weather in Shanghai and any overnight news about
      the projects in my memory. Keep it under 200 words.
    enabled: true
    webhook: ""        # optional HTTP POST target, e.g. an ntfy topic
```

Supported schedules, chosen so the file stays readable without a cron
dependency:

| Form | Meaning |
|---|---|
| `every 15m` | every 15 minutes (also `h`, `d`; minimum 5m) |
| `daily 07:30` | every day at local time |
| `weekly mon 09:00` | every Monday at local time |

Semantics:

- `next_run` is persisted in `.state/scheduler.json`, so a restart does not
  duplicate a run. A task with no state is scheduled forward from now, not
  immediately.
- After downtime, a missed run fires once if it is within `tasks.catch_up_seconds`
  (default 3600) and is otherwise skipped to the next occurrence. A daily brief
  from three days ago is noise, not a catch-up.
- Each run creates a conversation with `source: task`, executes the same agent
  loop, and appends one JSON line to `tasks/runs.jsonl` with status, duration,
  conversation id and error.
- Delivery is `store` always, plus an optional `webhook` POST
  (`{"task_id", "title", "text", "conversation_id"}`). A webhook failure is
  logged and recorded; it does not lose the run.
- `POST /assistant/tasks/{id}/run` executes a task immediately and does not
  move its schedule, which is how a new task is tested.
- The loop ticks every `tasks.tick_seconds` in the API process. When the API is
  down, nothing runs; that is the documented availability model for this slice
  and the reason the run history is on disk.

## 8. HTTP API

Mounted under `/api/v1`. Authentication reuses the bearer sessions; when
accounts are disabled the local owner is used. An unknown or malformed
conversation id is a 404; ids are validated before they reach the filesystem, so
path traversal is not expressible.

| Method | Path | Purpose |
|---|---|---|
| POST | `/assistant/chat` | send `{message, conversation_id?}`, get the reply |
| GET | `/assistant/conversations` | recent conversations, newest first |
| GET | `/assistant/conversations/{id}` | full transcript (tool blocks included) |
| GET | `/assistant/memory` | memory files with entry counts |
| GET | `/assistant/memory/search?q=` | lexical retrieval with provenance |
| POST | `/assistant/memory/distill` | run the distillation now |
| GET | `/assistant/tasks` | definitions plus persisted next-run state |
| POST | `/assistant/tasks/{id}/run` | run a task now |
| GET | `/assistant/ui` | mobile-friendly chat page |
| GET | `/assistant/status` | model, search provider, counts, scheduler state |

The chat response is non-streaming in this slice. Streaming is an additive
change behind the same route (SSE), and the page is written so it can switch
without moving state.

## 9. Configuration

Added to `apps/api/config.yaml`; defaults shown in `AssistantConfig`.

```yaml
assistant:
  enabled: true
  root: ../../data/assistant
  timezone: Asia/Shanghai
  # Empty means: reuse llm.base_url, llm.model and llm.api_key_env.
  model: ""
  max_history_messages: 30
  max_tool_rounds: 4
  max_tool_output_chars: 4000
  search:
    provider: bing_rss       # bing_rss | fake
    endpoint: https://cn.bing.com/search
    max_results: 5
    timeout_seconds: 15
  memory:
    enabled: true
    # Additional read-only Markdown roots indexed for retrieval.
    extra_roots: []
    digest_chars: 2000
    auto_distill: true
    max_facts_per_run: 12
  tasks:
    enabled: true
    file: tasks.yaml
    tick_seconds: 30
    catch_up_seconds: 3600
    webhook_timeout_seconds: 10
```

Secrets stay in the environment: `WINNIE_LLM_API_KEY` by default. The service
refuses to start with a clear error if the model is configured but the key is
missing, rather than failing on the first chat.

## 10. Security and privacy

- **Local files only.** Conversations, memory and run history are written under
  `assistant.root`. Nothing is sent anywhere except the configured model and
  search endpoints.
- **Auth.** When accounts are enabled every assistant route requires a bearer
  token. The instance is single-user (registration is closed by default), so the
  token is an access control, not a tenancy boundary. The UI stores it in
  `localStorage` and sends it as a header; it is never placed in a URL.
- **SSRF.** `fetch_page` accepts only `http`/`https`, resolves the host, and
  rejects loopback, private, link-local and reserved addresses; redirects are
  followed manually with the same check per hop.
- **Prompt injection.** Search results and fetched pages are untrusted data.
  The system prompt says so, tools have no side effects beyond the memory
  directory, and a memory write is attributed to a conversation so a poisoned
  fact can be traced and deleted.
- **Search terms.** The default provider is Bing's RSS output, which its terms
  allow for personal, non-commercial use in an aggregator. A paid search API
  can replace it through the `SearchProvider` port.
- **No credentials in files.** The model key is read from the environment; the
  conversation log contains no headers or keys.

## 11. Delivery plan

| Step | Deliverable | Exit criteria | Status |
|---|---|---|---|
| A1 | Conversation store, memory files, index, distillation | A chat turn lands in a file; distillation writes a fact; search finds it | In progress |
| A2 | Agent loop, tools, HTTP API | A real model answers with a web result; tool trace is in the file | In progress |
| A3 | Scheduler, run history, webhook delivery | A declared task fires, writes a conversation and a run line | In progress |
| A4 | Chat page, docs, verification | Phone-sized page sends and renders a message; suite passes | In progress |

Follow-ups after this slice, in order:

1. Streaming replies over SSE.
2. Task creation from chat (`schedule_task` tool) with a confirmation step.
3. Readability extraction for `fetch_page`: portal pages put the answer below
   navigation, so the tool currently returns a truncated page head and the
   model has to cope. That worked on a weather page and will not scale to
   article-heavy reading.
3. Vector retrieval once an embedding provider is configured.
4. Delivery adapters beyond webhook (ntfy/Telegram) behind one port.
5. A background worker so the assistant survives API restarts.

## 12. Verification

Automated:

```bash
cd apps/api && .venv/bin/python -m pytest -q
```

Covers the store round-trip, id validation, distillation idempotency,
lexical retrieval, the tool loop with a fake model, schedule computation,
scheduler state and the HTTP routes.

Live, with a real model and the network:

```bash
export WINNIE_LLM_API_KEY=...        # DeepSeek key
make api
curl -s localhost:8000/api/v1/assistant/chat \
  -H 'content-type: application/json' \
  -d '{"message":"用一句话说明今天上海天气，并给出处"}'
```

Then check, in order: the reply mentions a web source; the conversation file
under `data/assistant/conversations/local/` contains the `tool:web_search`
block; `POST /assistant/memory/distill` writes a line to a memory file; the same
query via `/assistant/memory/search` returns it; a task with `every 5m` appears
in `tasks/runs.jsonl` after a tick.

## 13. Open questions

- Which push channel is the real one for this user: ntfy, Telegram, or a
  self-hosted webhook? The interface is one POST; the default is unset.
- Should the assistant answer with citations by default, or only when asked?
  Currently the tool output carries URLs and the prompt asks for a source when a
  web result was used.
- Is `memory/` the right home for facts that belong to a project note, or
  should confirmed facts be promoted into `data/kb/`? This slice keeps them in
  memory and leaves promotion manual.
