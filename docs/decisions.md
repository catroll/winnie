# Decisions

Architecture and dependency choices that are not obvious from the code. Each
entry records the alternative that was rejected and why.

## One module for every model vendor, keys in its own `.env` (2026-11-01)

**Decision.** `packages/winnie-llm` owns every vendor call — OpenAI-compatible
chat and tool calls, recording-file ASR and streaming captions — plus the
supplier/model/feature registry that used to live in `apps/api/src/winnie_api/ai`.
Vendor credentials live in `packages/winnie-llm/.env`, and the variable is
derived from the supplier id (`deepseek` → `DEEPSEEK_API_KEY`, an unknown id →
`<SUPPLIER>_API_KEY`). The registry stores neither a key nor a variable name.

**Why.** The calling code had drifted into four places: `winnie-core.llm` owned
the chat client, `winnie-meetings` owned both ASR adapters, `apps/api` built
clients from the registry, and `apps/api/news` and the meeting adapters each
constructed their own. Adding a provider meant editing several packages, and
each integration carried an `api_key_env` whose value only the API process
happened to have. A capability could not be tested or reused without knowing how
the host loaded its environment.

**Rejected: keep the registry in the host and move only the clients.** It would
have left provider selection and key naming split across two packages, so
"which model does this feature use, and with which key" would still require
reading both. Moving the registry made the module deep enough to own the whole
question; the host keeps only the HTTP console and the first-start seed.

**Rejected: keep `api_key_env` per integration.** Two integrations for one
vendor could then point at two variables, but nothing owned the set of names:
a typo produced a runtime "variable is empty" instead of a startup-visible
mapping, and the console had to ask the owner to invent a name. The fixed
mapping makes the console show the exact variable and whether it is set, and
makes a second account a different supplier id (`deepseek-work` →
`DEEPSEEK_WORK_API_KEY`).

**Migration.** A `version: 5` registry still contains `api_key_env`; the loader
strips it before validation and rewrites the file as `version: 6`. A v1 file's
combined internal route is still recognised from that legacy value, so the
existing migration chain keeps working.

## Android client (2026-10-31)

**Stack.** Kotlin 2.4.21, Jetpack Compose with Material 3, a single
`ComponentActivity`, and manual dependency construction in `AppContainer`.
No navigation or DI framework: the MVP has four screens and one object graph,
and a navigation library plus Hilt would add build plugins and a second way to
construct view models before the screen set is stable. Add them when a back
stack, deep links or a second module make them pay for themselves.

**Build.** Android Gradle Plugin 9.5.0 with its built-in Kotlin support,
Gradle 9.8.2, `compileSdk = 37`, `compileSdkMinor = 2`, `targetSdk = 37`,
`minSdk = 26`. The Kotlin version is pinned through a `buildscript` classpath
entry so built-in Kotlin, the Compose compiler plugin and the serialization
plugin all use 2.4.21 instead of AGP's older bundled Kotlin. The current
AndroidX artifacts (core 1.19.1, Compose BOM 2026.09.00, OkHttp 5.5.1) require
API 37; API 36 was rejected by the AAR metadata check.

**HTTP.** OkHttp 5.5.1 with hand-written `kotlinx.serialization` requests
instead of Retrofit. The API has eight endpoints; the Retrofit dependency and
its converter would mostly restate the same DTOs, while this client is directly
usable from a JVM integration test. When the endpoint count grows past roughly
twenty, or when interceptors for retries/metrics are needed, Retrofit becomes
worth its layer.

**Audio.** `AudioRecord` at 16 kHz mono 16-bit PCM, streamed into a WAV file
whose header is patched on close. This is smaller than the emulator's default
44.1 kHz and is accepted by every ASR vendor; no chunking or compression is
needed before the 500 MB server limit. Recording is owned by the recorder
screen's view model, not a foreground service, because the MVP is verified
without a device and background recording is a follow-up
([todo](../docs/todo.md)). A phone that backgrounds the app can lose the tail
of a recording until that follow-up lands.

**Token storage.** AES/GCM with a key in the Android Keystore, wrapped in
`SharedPreferences`, rather than `androidx.security:security-crypto`. That
library is deprecated and its replacement guidance is to use the Keystore
directly. A plain preference would expose the bearer token to anyone who can
read the app's data.

**Server address.** `WINNIE_PUBLIC_URL` is read at build time and baked into
`BuildConfig.DEFAULT_SERVER_URL`, so one checkout can produce an APK for the
emulator, a LAN address or a public IPv6 domain without editing source. The
fallback is `http://10.0.2.2:8000`; `make android` loads the value from
`apps/api/.env`. A hard-coded constant was rejected because it made every
non-emulator build a source change.

**Cleartext HTTP.** Allowed through `network_security_config.xml` because the
MVP talks to a laptop on the LAN or to `10.0.2.2` from the emulator. A build can
also point at a public IPv6 domain, but that path is still cleartext: bearer
tokens, meeting audio and transcripts cross a globally routable network
unencrypted. This is a development affordance, not a deployment choice. A real
deployment must terminate TLS, and the network-security config should then be
narrowed to that domain.

**Verification.** This machine has no `/dev/kvm`, so an Android emulator cannot
run. Verification is therefore: a debug/release APK build, JVM unit tests for
the HTTP and WAV code, a Robolectric Compose smoke test for the login screen,
and a JVM integration test that drives the app's own client through the real
backend. A physical-device run with the real ASR key is still required and is
tracked in [todo](../docs/todo.md).

**Interface language.** English. The user writes Chinese, but the project rule
is English for all artifacts, including app strings; localisation is a later
step, not a reason to mix languages now.

**Live captions.** The recorder creates the meeting before it starts and opens
`WS /api/v1/meetings/{id}/stream`; the server proxies 16 kHz mono PCM to
Volcengine SAUC 2.0 and returns cumulative text. The vendor key stays on the
server, and the Android app never calls the vendor. The WebSocket is not a
second processing pipeline: stopping still uploads the WAV and runs the file
ASR, translation and minutes path, which replaces the provisional captions.
That costs a second ASR pass per meeting, but it keeps one canonical
transcript, reuses the validated pipeline, and makes a dropped stream a caption
failure rather than a lost recording. Persisting live segments would remove the
second call and is a deliberate follow-up, not an oversight. Live captions are
also the reason the meeting row is created at record start; discarding deletes
that draft, and a process death can leave an empty draft until recovery lands.

## Management console: AdminLTE 4 on Vite + React (2026-11-01)

**Template.** AdminLTE 4.10.0, the current release (published 2026-10-01), with
Bootstrap 5.3.8, Bootstrap Icons 1.13.2 and self-hosted Source Sans 3.
AdminLTE 3 was rejected because it is the older jQuery line; 4 is the maintained
Bootstrap 5 line and needs no jQuery. The console uses AdminLTE's own layout,
login box, cards, small boxes, nav tabs, colour-mode control and accessibility
helpers. Application CSS is limited to a full-height React root and
`white-space: pre-line` for transcript text: AdminLTE's static pages put
`.app-wrapper` or `.login-box` directly under `<body>`, while a SPA puts the
React root there instead. No custom theme or component layer was added.

**Application shell.** Vite 8.3.5, React 19.3.1, React Router 7.18.5,
TypeScript 7.0.2. React is the management-UI stack already recorded in the
project; AdminLTE supplies the visual system rather than a React component
library. Vanilla TypeScript was rejected because the console has enough state
(session validation, pagination, tabs, independent async errors) that a
component model keeps the code readable. A component library such as shadcn/ui
was rejected because it would replace AdminLTE rather than use it.

**API boundary.** Same-origin only by default. In development Vite proxies
`/api`, `/healthz`, `/readyz` and `/routes`; in production a reverse proxy must
do the same. The API has no CORS middleware, and adding it for a personal
console would widen the attack surface for no gain. `VITE_API_BASE_URL` exists
for a deliberate cross-origin deployment, but its README records the CORS
prerequisite.

**Session.** The opaque bearer token lives in `sessionStorage`; **Remember me**
moves it to `localStorage`. No cookies means no CSRF surface. The token is never
rendered or logged. A 401 from an authenticated request clears both stores and
the route guard returns to login. Changing the password revokes every server
session, including the current one, so the console signs out and explains why on
the login page.

**Scope.** The console lists, reads, exports, regenerates and deletes existing
meetings, and inspects gateway routes. Recording and upload stay in the Android
client; account creation and route edits stay with the API/config. This keeps it
an operator view instead of a second capture path.

**Time handling.** SQLite stores meeting timestamps without an offset, so the
client treats a timestamp with no zone designator as UTC before formatting it in
the browser's local time. `expires_at` already carries `Z`.

**Verification.** `npm run build` (TypeScript 7 plus Vite 8) and a real Firefox
session against the API verified password login, dashboard data, meeting list
and detail, transcript/minutes tabs, Markdown export, minutes regeneration,
meeting deletion, gateway routes, the account page, password change and
re-sign-in, dark mode, the mobile sidebar, and redirect to login after the
server session was revoked. There is no committed frontend test suite yet; the
browser checks were run against a disposable copy of the database.

## Assistant scheduled tasks in the console (2026-11-01)

**Read-only by design.** The console renders `GET /api/v1/assistant/tasks`
(schedule, active state, next and last run, last result, prompt, webhook) and
has no create, edit, pause or cancel controls. The assistant owns
`tasks/tasks.json` through the `schedule_task`, `list_tasks` and `cancel_task`
chat tools, and the scheduler holds next-run state in memory. A console writer
would be a second writer to the same file and would have to re-synchronise that
state, duplicating a path that already works conversationally. The console's job
here is to make an unattended mechanism inspectable, which is exactly the gap a
chat-only task list leaves.

**One result inline, the log on request.** The task table shows the last
persisted result; the Run history card reads
`GET /api/v1/assistant/tasks/runs` with a task filter and offset pagination, so
every execution is inspectable. `tasks/runs.jsonl` remains the only store: the
endpoint scans it and keeps only the requested page in a bounded deque, so a long
log costs a scan, not unbounded memory. The response carries `total` and
`skipped` (malformed lines), so the page can say how much history exists and
whether a partial line was ignored. Copying runs into the task definition was
rejected: it would grow the definition without bound and create a second source
of truth. Reading the file directly from the browser was rejected because the
file is on the API host.

**Status route is optional.** The page also reads `/assistant/status` for the
timezone and the scheduler's running flag, but tolerates that call failing: the
task list still renders, with the scheduler box marked unavailable. A task list
that disappears because a secondary status call failed would hide the data the
page exists to show.

## Assistant: files first, no orchestration framework yet (2026-11-01)

**Superseded in part.** The storage location, memory retrieval and scheduling
parts of this entry were replaced later the same day; see
[Assistant: index-backed memory and conversation-managed tasks](#assistant-index-backed-memory-and-conversation-managed-tasks-2026-11-01).
The tool-loop, search-provider, model-client and default-model decisions below
still stand.

**Storage.** One Markdown file per conversation under
`data/assistant/conversations/<day>/`, append-only, with the tool trace as
`## tool:<name>` blocks. The alternative — a database table plus a JSON export —
was rejected because it makes the record only readable through the service,
which contradicts the project rule that documents are the source of truth. The
instance is single-user: `auth.allow_registration` is off, and the bearer token
protects the API instead of partitioning data. A per-owner directory was
considered and dropped because scheduled tasks run without a session and would
have produced files the owner could not see.

**Memory.** A lexical index over the Markdown entries (ASCII words plus CJK
character bigrams), rebuilt from the files at startup and after every write.
Qdrant plus an embedding model is the designed end state, but this checkout has
no embedding provider, and a lexical index that can be rebuilt and inspected is
verifiable today; the agent only touches `MemoryIndex.search`, so the swap is
one class. The bigram tokenizer was chosen over a Chinese segmenter dependency:
for a personal corpus of thousands of entries it retrieves well enough, and its
behaviour is inspectable.

**Orchestration.** An explicit tool loop instead of LangGraph, which
`DESIGN.md` names for the assistant layer. The current agent has one model, four
tools and a four-round bound; a graph framework would add a dependency and hide
the per-turn trace that is now stored in the conversation file. The loop is
behind `AssistantAgent`, and the exit condition is written down: adopt LangGraph
when there are real branches (planner, subagents, per-step retries), not before.

**Scheduling.** No `croniter` and no APScheduler. Tasks use three readable
forms — `every 15m`, `daily 07:30`, `weekly mon 09:00` — and next-run state is
persisted in `.state/scheduler.json`. Cron expressions buy flexibility a
personal assistant does not need yet and bring DST/parse edge cases that would
have to be tested; a new dependency also means a lockfile change for a feature
that is currently three branches. Revisit when monthly or multi-field
schedules are requested.

**Search.** Bing's RSS endpoint by default (`format=rss`), which needs no API key
and is explicitly intended for personal aggregation. DuckDuckGo HTML and the
Brave API were unavailable from this host (DuckDuckGo timed out; Brave needs a
key). The `SearchProvider` port is one method, so a paid API is a new class, not
a refactor.

**Model client.** `winnie_core.llm.OpenAICompatibleLLM` gained a `chat()` method
that accepts tool schemas and returns tool calls; `complete()` now delegates to
it. Reusing the client keeps the retry, timeout and usage handling identical
between translation/minutes and the assistant, instead of a second HTTP client
inside the capability. Tools are only added to the request body when passed, so
the existing job payloads are byte-identical.

**Default model (superseded 2026-11-01).** Originally `assistant.model: fake`
in `config.yaml`, matching the meetings `translator`/`summarizer` fake defaults.
The registry now owns the model and a fresh seed binds `assistant.chat` to
`fake`; `assistant.model` and the `llm` block are gone. A missing integration key
must not silently downgrade to a real-looking answer, and it must not stop the
gateway and meeting APIs from starting; the chat route returns `502` naming the
missing variable.

**Chat surface.** A self-contained page served at `/api/v1/assistant/ui` rather
than a panel in `apps/admin`. The console is a separate, still-uncommitted
deliverable, and the chat page must work on a phone over the LAN with no build
step. It stores the bearer token in `localStorage` and uses `textContent` for
rendering, so model output cannot inject markup. *(Superseded in part: the
console was committed later and now has its own chat page; see
[Admin console: assistant chat and channel-tagged conversations](#admin-console-assistant-chat-and-channel-tagged-conversations-2026-11-01).
The phone page remains because it needs no build step and is the LAN surface.)*


## Assistant: index-backed memory and conversation-managed tasks (2026-11-01)

**Storage moved out of the repository.** The assistant's files live in
`~/Documents/Mine/Winnie`, not `data/assistant`. Two reasons: the user asked for
the location, and it puts the files inside the index's existing `Documents/Mine`
root, so memory and conversations are indexed by the same sweep as the owner's
notes. Keeping them in the checkout would have required a second root and would
have mixed personal records into the code repository. `tasks.json`, `runs.jsonl`
and the JSON under `.state/` are not `.md`/`.txt`, so they are not indexed.

**Retrieval is `winnie-index`, not a second index.** The in-process lexical
`MemoryStore.search` and its tokenizer were deleted. The index already does
mixed Chinese/English tokenisation, chunking by heading, BM25 ranking, dedupe
and read-time revalidation; a second implementation would drift from it and
double the code that has to stay correct. The assistant sends `POST /search` and
reads windows with `GET /doc`. The pinned profile digest stays in process: it is
a bounded slice of the system prompt, not a retrieval result, and it must be
current on the same turn a fact is written.

**The index has no path filter, and this is acceptable.** `search_documents`
searches the whole root rather than only `Winnie/`. A separate root or a new
`prefix` parameter was considered and rejected for now: the owner's notes and
the assistant's memory are one corpus on purpose, every hit carries its path so
the model can tell them apart, and changing the running daemon's API or config
to isolate a few files is not worth it before the noise is measured.

**Tasks are created in conversation.** `tasks/tasks.json` is written by the
assistant, not by the user. The earlier `tasks.yaml` was a config file the user
had to edit by hand, which contradicted "the assistant manages its schedule".
Ids are a slug plus a random suffix so a Chinese title still yields a usable id.
The model must restate the schedule and content and wait for an explicit yes
before calling `schedule_task`; the confirmation is in the system prompt and the
tool description because it is a product requirement, not a suggestion.

**A task run cannot manage tasks, and hiding a tool is not enforcing it.** The
scheduler passes the agent a tool set without
`schedule_task`/`list_tasks`/`cancel_task`, and the tool dispatcher refuses
those names when the turn is a task run. Both are needed: the first test run
emitted `schedule_task` from a task turn even though the schema was not offered,
and the agent executed it, because the loop trusts the model's tool name. The
schema filter keeps the model from *seeing* the tool; the dispatcher check keeps
a wrong or adversarial name from *working*. `test_a_task_run_cannot_create_tasks`
is the regression guard.

**No pause/update tool yet.** Cancelling and recreating is the current edit
path. `enabled: false` in the JSON is honoured by the scheduler, so a pause is
expressible in the file, but no tool writes it; adding one before there is a
real request would be surface area without a user.

**Freshness is split and documented.** Pinned profile facts are in the prompt on
the same turn. Everything else waits for the index sweep (300 s by default) or a
build. Promising immediate recall would be false; the conversation history
covers the gap for the current session.

## Admin console: assistant chat and channel-tagged conversations (2026-11-01)

**Two chat surfaces, one route.** `/chat` in the console and
`/api/v1/assistant/ui` on a phone both call `POST /api/v1/assistant/chat`. The
console version was added because the console is now committed and is where the
owner works at a desk; the phone page stays because it needs no build step.
Neither surface owns state: the conversation file is the record, so a reply
starts in the console and can be read from the phone.

**`channel` is separate from `source`.** `source` already meant who started the
turn (`chat` or `task`). Adding the transport to the same field would have made
a future Telegram adapter a new *source* and broken the scheduler's
`source == "task"` rule. `channel` records where the turn arrived (`admin`,
`web`, `api`, `task`) in the front matter, so the file is self-describing and
the history filter needs no database. The field is client-declared; that is
acceptable for a single-user instance where it is a label, not an access
boundary. `task` is reserved for the scheduler and rejected by the chat schema.

**Task runs are read-only in the console.** A task-sourced conversation is the
record of an unattended run. Appending a human turn would mix two kinds of
record and would run the model with the scheduled prompt and without the
task-management tools. The store rejects the append (`ConversationReadOnly`,
`409` from the API) and the console disables the composer rather than relying on
the UI alone.

**Non-streaming is deliberate for this slice.** ChatGPT-like layout is the
requirement; token-by-token rendering is not. A turn waits for the stored reply
and shows a thinking indicator, then re-reads the transcript so tool blocks the
chat response does not return are visible. SSE remains an additive change behind
the same route.

**Plain text, not Markdown rendering.** The reply is rendered with
`white-space: pre-line` and React text nodes. A Markdown renderer would add a
dependency and an injection surface for a personal console whose record is
already readable; the stored file remains the faithful view.

**AdminLTE's `direct-chat` is reused.** It supplies the bubbles, timestamps,
avatars and float direction. Its `.direct-chat-messages` is fixed at `250px`
because that is the height of AdminLTE's own demo, so one additive rule sets a
usable `clamp()` height; no other chat CSS or component layer was added.

## Admin-managed AI providers (2026-11-01)

**One registry, one binding per feature.** The console owns suppliers,
endpoints, models and a map from feature to endpoint/model. A feature is the
stable unit (`assistant.chat`, `meetings.transcribe`, `meetings.translate`,
`meetings.summarize`); the endpoint is the replaceable unit. The alternative — a
provider name inside each feature's YAML block — would have duplicated endpoint
and key settings four times and made "switch the assistant to DeepSeek" a
multi-key edit. It would also have left the gateway route table as a second,
unrelated list of the same vendors.

**The supplier is the unit the user configures; endpoints and models hang off
it.** One DeepSeek supplier owns up to three endpoints (OpenAI-compatible
gateway, Anthropic-compatible gateway, internal OpenAI-compatible) and one model
catalog. Endpoints remain separate records because the protocol, gateway paths,
payloads and key role differ; collapsing them would require a protocol dimension
anyway and would break capability validation. Models belong to the supplier, not
the endpoint, because the same `deepseek-chat` id is valid on every DeepSeek
endpoint; putting the catalog on each endpoint would duplicate it and make
"which models exist" ambiguous. The console therefore groups endpoints under the
supplier and shows one **Models** editor per supplier. This is the presentation
the earlier protocol-suffix decision deferred.

**The state is a YAML file, not a database table.** `data/ai.yaml` is
human-readable, easy to back up, and works when the database is disabled
(a gateway-only or assistant-only deployment). The index daemon already owns
its configuration the same way. The database would have bought multi-writer
concurrency and transactional history, neither of which a single-user console
needs, and would have made AI provider resolution impossible without `auth` or
`meetings` enabled. Writes are atomic (temporary file plus `os.replace`) and the
file is mode `0600`.

**`config.yaml` seeds once; it is not a second live source.** On first start the
registry is generated from `routes`, the Volcengine ASR block and the feature
enable flags. After the file exists the console is authoritative. There is no
YAML fallback for a feature: an unbound feature has no provider and fails with a
named error. Re-reading the YAML on every resolve was rejected because it makes
"the console changed something" indistinguishable from "an operator edited a
file".

**Features bind to models, not to endpoints.** The first supplier iteration
still made a feature choose an endpoint *and* a model, so the console showed
"Internal (OpenAI-compatible)" and gateway/internal records next to a feature.
That is the wrong seam: the user's choice is the model; the endpoint is an
implementation detail resolved from the model's supplier. Version 4 bindings are
`vendor` + `model`; the server finds the supplier's internal API (never a
gateway route), and the console has one **Model** select grouped by supplier.
The v3 `integration_id` value is kept only for reading/migrating old files and is
cleared after migration. This also lets the model catalog be supplier-wide and
refreshed with one **Sync models** call, instead of repeating model ids on
endpoints. The trade-off is that a supplier can have only one internal API for a
given protocol; that is intentional, because multiple credentials for the same
supplier would be a second account, not a second model.

**Keys moved to the winnie-llm environment (superseding the earlier
`api_key_env` rule).** The key-value file is `packages/winnie-llm/.env`, read
only by `winnie-llm`; the variable name is derived from the supplier id, so the
registry stores neither the key nor its name. The alternative — keeping a key
variable per integration — was rejected because a typo became a runtime "variable
is empty", the console had to ask the owner to invent a name, and capability
packages had no way to know which environment the host would load. The old
alternative, encrypting keys in the registry, stays rejected for the same
reasons as before: a master-key lifecycle and a re-encryption migration are too
much for a single-user instance. A key still never appears in an API response or
in a browser. The cost is unchanged: rotating a key needs an API restart.

**Resolution is per call, not per process.** The assistant, meeting service and
worker are built once, but their provider ports are thin wrappers that resolve
the current binding on every operation. Restarting the API to apply a console
change would have made the page a configuration editor for the next boot rather
than a control surface. Gateway routes are refreshed on every CRUD mutation in
the API process; a second API worker would see a new route on its next restart,
which is why a single API process remains the simple deployment and is stated
in the operations doc.

**There is no second model configuration.** `config.yaml` used to carry an
`llm` block, `assistant.model`, `meetings.translator` and
`meetings.summarizer`. Keeping them meant the console could say one thing while
a fresh start silently used another, and the old `llm` block was the only way to
configure the internal model. They are gone: the registry is the only source for
base URL, model and binding. A fresh registry leaves model
features unbound; the owner adds a supplier API and model in the console. Runtime
limits (timeouts, retries, token caps, chunk sizes) move with the choice into
`data/ai.yaml` as a `runtime` section, editable in the console. The v1 migration
still recovers an existing `internal-llm` from the file, because that record is
the only evidence of the old `llm` configuration; it is then a normal endpoint.
v2 files are grouped under suppliers and their endpoint defaults/bound models
become the supplier model catalog; v3 files are converted to `{vendor, model}`
bindings; v4 files have their fake supplier removed and fake bindings cleared;
the schema is version 6, with the key variable removed.

**Gateway exposure is separate from server-side credentials.** A vendor endpoint
can be a public proxy route (`gateway: true`), an internal provider
(`gateway: false`), or both. The first version of the seed matched a route by
base URL and reused it as the internal LLM, which put the server-side key on a
public route. v2 always creates a separate `internal-llm` record with
`gateway: false`; the route stores no key and forwards the client's key. A v1 file is migrated in place on the next read. The alternative —
one record with a per-role key — was rejected because the two roles can use
different keys, different owners and different lifetimes; a single `api_key_env`
cannot express that, and combining them is what caused the original confusion.

**The protocol suffix identifies an endpoint within one supplier.** The kind is
the wire format and adapter, so DeepSeek's OpenAI and Anthropic endpoints must
remain separate records: the gateway paths and payloads differ, and the internal
adapters only speak OpenAI `/chat/completions`. They are no longer presented as
separate suppliers: the console groups them under one **DeepSeek** supplier.
Gateway rows are public proxy routes; the internal API is edited with **API** and
is never shown as a feature target. The endpoint `name` stays the gateway path
prefix; the supplier `id` is the grouping key and the models are shared.

**Kind/model and credentials are validated at the binding.** An ASR endpoint
cannot serve `assistant.chat`, an OpenAI-compatible endpoint with no model
cannot serve a model feature, a model must be in the supplier catalog, and a
gateway-only OpenAI endpoint cannot serve an internal feature.
Catching that while the binding is saved gives the user an immediate, named
error instead of a failed meeting pipeline later. The same validation runs on
load, so a hand-edited file fails loudly. Removing a model that an endpoint
default or binding still uses is rejected for the same reason.

**The fake provider is test-only.** It used to be a first-class integration so
the console was usable offline. That put an unconfigured model in a product where
the requirement is "every feature calls the model selected in the registry".
The seed no longer creates a fake supplier; the admin hides the kind, and the v5
migration removes an existing fake supplier and clears its bindings. The
deterministic fake classes remain for unit tests and are never a product default.

## LLM usage ledger and provider balances (2026-11-01)

**One JSONL ledger shared by internal jobs and the gateway.** A call through
`/{route}/v1/...` and a call made by the assistant or a meeting job differ in
who holds the key, not in what should be counted. Recording both in one place is
what makes the console's total meaningful. The alternative — reusing the
gateway's ask/answer files — was rejected because those files hold full prompts
and responses and are unsuitable to scan for aggregates. The ledger keeps only
counts, identifiers and latency.

**Append-only month files, not a database table.** `data/usage/usage-YYYY-MM.jsonl`
is human-readable, survives a database being disabled, and needs no migration
for a high-volume event stream whose only reader is a personal console. A table
would buy indexed queries, but at one person's call volume the page scans at
most twelve files, and SQLite would have made gateway-only deployments (no auth,
no meetings, therefore no database) unable to record. The cost is explicit: a
year-long range is capped, and pruning is manual. Writes are one `O_APPEND`
`write(2)` per line, so concurrent requests cannot interleave a record.

**Prices are configuration, not ledger data.** An event stores the tokens the
provider reported; `usage.prices` is applied when the page is read. That means a
corrected price also corrects history, and the ledger never has to be rewritten.
Putting prices on the AI integration was rejected for this slice: the registry
describes routing and credentials, while a price is deployment billing data that
differs by contract and changes with vendor pages. It also cannot express the
peak/off-peak rates DeepSeek now bills, so a price row is an estimate by
definition. No prices ship with the repository; an unlisted model is counted as
unpriced instead of being estimated at zero, which would look like free usage.

**Missing token classes are `null`, not zero.** OpenAI's prompt count includes
cache hits, Anthropic's input count excludes them, and a streamed OpenAI
response has no usage at all unless the client asked for it. The ledger stores a
normalized, uncached input count so one price row applies, and it keeps
`unpriced` (tokens known, no price) separate from `unreported` (no tokens). A
stream without a usage chunk therefore cannot silently become a free call.

**A provider-reported cost wins over the local estimate.** OpenRouter returns
the actual cost with the response; trusting it is strictly better than
recomputing from a table. The table is the fallback for vendors that do not
report cost. Provider-reported costs are assumed to use the configured
`usage.currency` (USD by default); a deployment that bills in another currency
must keep the table consistent with it, which is documented rather than guessed
at.

**Provider balances are chosen by host, not by a new registry field.** The
endpoint is a property of the vendor (DeepSeek `/user/balance`, OpenRouter
`/api/v1/credits`, Moonshot `/v1/users/me/balance`, SiliconFlow `/v1/user/info`),
and adding a vendor is one adapter plus one host entry. A new `balance_kind`
field would have added a second thing to configure for every integration and
would still not cover unknown hosts. An integration whose host has no adapter is
shown as **not exposed** with a neutral explanation.

**Balances use server-side keys only.** The gateway forwards the client's own
vendor key and stores none, so a proxied call consumes the client's account and
its balance is invisible here. The card states that limit instead of implying
the number covers every request. The ledger still records proxied calls because
usage comes from the response, not from the key.

**Recording is best-effort.** A failed append is logged and swallowed; a
telemetry disk error must never turn a successful model call into an error. The
same rule covers the balance query: one provider's failure becomes an error on
its row, not a failed page.

## Local file and command tools: opt-in, confirmed, audited (2026-11-01)

**The assistant has to act, not only read.** The first slice deliberately had
no shell and no file write outside `profile/`. The owner asked for a personal
assistant that can inspect and change local files and run local commands, which
is a real capability, not a prompt tweak. It is therefore a separate, opt-in
layer with its own controls rather than a small widening of the existing tools.

**File tools are confined by configuration.** `assistant.workspace.root` and
`extra_roots` are the only allowed trees; every path is resolved (following
symlinks) and checked before use. The assistant's own `conversations/`,
`profile/`, `tasks/` and `.state/` are always refused, as are common credential
directories, `.env`, private-key suffixes and `.git` writes. This is path
confinement, not an OS sandbox: the command tool is the escape hatch, and it is
gated.

**Overwrites carry the hash they read.** `read_file` returns a sha256 and
`write_file(mode=overwrite)` requires it; the writer re-checks the hash before
replacing the file. That prevents a blind or stale overwrite, which is the most
damaging file operation. Existing files are copied to `.state/backups/` first.

**Commands are not a sandbox.** They run as the API user, with the process
timeout, an output cap, a minimal environment (no `WINNIE_*` or vendor keys) and
a JSONL audit line. A hard allowlist was rejected as the default because the
owner asked for general system commands, and an allowlist that includes a shell
or interpreter is not a boundary anyway. `exec.allow` remains available as a
guardrail. A real sandbox (bubblewrap, firejail or a container) is a follow-up
and is stated as such in `assistant.md`.

**Confirmation is a turn boundary.** A command or an existing-file write returns
a `PENDING` id and changes nothing. `confirm_action` executes it only when the
next user message is an explicit, short confirmation, and only when the pending
action was created in an earlier turn. A model cannot self-approve by calling
the tool twice in one turn, which is exactly what a prompt injection would try.
The pending action expires after `pending_ttl_seconds` and is discarded by a
non-confirmation message.

**Scheduled runs get neither group.** A task run has no human turn to confirm,
so it gets no file or command tools. This is enforced by the tool schemas and by
`ToolBox.execute(allow_local=False)`, not by a prompt rule.

**The default roots are documents and projects, not all of home.** The owner
asked for local access; the assistant's own storage and the usual credential
paths are refused, and reading every dotfile in `$HOME` by default would have
been a silent privacy expansion. `~/Documents/Mine` plus `~/Projects` cover the
owner's notes and code; a deployment with files elsewhere changes
`workspace.root`/`extra_roots`, not code. The command tool can reach the rest of
the machine after confirmation, which is the deliberate escape hatch.

## Local execution moved to winnie-local, with an LLM review and a whitelist (2026-11-01)

**A package owns the whole path, not a tool handler.** The first local-tool
slice put `Workspace`, `CommandRunner`, `PendingActions` and the side-effect
policy inside the assistant's `tools.py` path. That made the assistant both the
proposer and the executor, and every future tool would have had to remember the
plan/review/confirm/audit sequence. `packages/winnie-local` now owns planning,
path confinement, the whitelist, the LLM review, the pending confirmation and
the audit; `winnie-assistant` only builds a small `LocalAction` and calls
`LocalExecutor`. The architecture test allows `winnie-local` from the assistant
because it is a shared execution layer like `winnie-core`, not a capability.

**Every non-whitelisted action gets one LLM review before it can become a
pending confirmation.** The reviewer is a separate model call with a dedicated
security prompt; it sees the current user message and the action summary/preview
or argv/cwd, not the whole conversation. The prompt treats both as untrusted
data. The answer must be JSON with `allow`, `risk` and `reason`; a timeout,
provider error or malformed answer is a denial. The decision goes to
`.state/review.jsonl` without file contents or command output. The reviewer
reuses the bound `assistant.chat` model rather than adding a second model
configuration; a separate reviewer model is a named follow-up.

**Why not let the assistant's own reasoning be the review?** It is the party
proposing the action, and a prompt injection that changes the proposal would
also change its self-assessment. A separate call with a narrow prompt is not a
proof of safety, but it is an independent check and it fails closed. The human
confirmation remains after it; the review is not a replacement for the person.

**The default whitelist is deliberately read-only.** `list_files`, `read_file`,
`pwd`, `ls`, `cat`, `head`, `tail`, `wc`, and git `status`/`rev-parse`/
`ls-files` skip the review. The policy still checks their path arguments against
the workspace and refuses protected/credential paths. `git diff`/`log`/`show`
are reviewed because a repository can configure textconv or external-diff
helpers that execute. Adding a write command or a shell to the whitelist is
allowed by configuration, but it removes the review for that command and is the
owner's explicit risk.

**The review is a data flow.** The current user message and a bounded, redacted
action preview are sent to the model provider. The package cannot promise local
confidentiality for that preview; the docs say so. A deployment that needs it
must use a local reviewer model.

**The human gate stays.** A review allow does not execute: existing-file writes
and commands still return `PENDING` and need an explicit confirmation in a later
turn. A review denial returns `REVIEW DENIED` and creates no pending action.
Scheduled task runs still get neither file nor command tools, because there is
no human turn to confirm them.

## Chat is a module: winnie-chat owns the loop and the skills (2026-11-01)

**The assistant was calling the model itself.** `AssistantAgent` contained the
model loop, prompt assembly and tool schemas, so "chat" was a side effect of
the assistant's tool code. Add `packages/winnie-chat`: `ChatEngine` owns the
bounded model/tool loop, `SkillRegistry` owns what is offered, and
`build_system_prompt` assembles the persona, time, memory digest, pending
confirmation and the instructions of the enabled skills. The assistant's
`AssistantAgent` now only creates the conversation, records user/tool/assistant
blocks and stores the reply. The host can replace the engine without touching
conversation files.

**Skills are the unit of capability.** A `SkillManifest` declares a name, title,
description, prompt instructions, tools, permissions, scheduled-run safety and
whether it is toggleable. The assistant registers six: `core` (persona/rules,
always on), `web`, `documents` (the index), `memory`, `tasks` and `local`
(`winnie-local`). The registry rejects duplicate tool names, resolves a tool to
its owner, filters unavailable services and scheduled-unsafe skills, and
persists the enable/disable map in `skills/skills.json`.

**Disabling is a real removal, not a hint.** The chat engine builds the tool
schemas and system prompt from the enabled skills. A disabled skill's tools are
absent and its instructions are not sent; a hallucinated tool call is rejected
before execution. The core skill cannot be disabled, and `tasks`/`local` are
chat-only because scheduled runs have no human to confirm a schedule or a side
effect.

**The document index is a skill, not a special case.** `documents` owns
`search_documents`/`read_document` and the instruction to cite `path:line`; if
the daemon is off the skill is shown unavailable and is not offered. The local
execution skill owns the file/command tools and delegates to `winnie-local`, so
the review, whitelist, confirmation and audit path is not reimplemented or
bypassed by the skill layer.

**The admin views and manages the same registry.** `GET /assistant/skills`
returns the manifests plus enabled/available state, tools, permissions and
instructions; `PUT /assistant/skills/{name}` toggles one. The console `/skills`
page renders that list and calls the toggle route. This is a view of the
registry, not a second configuration file: the state lives in one place and the
next chat turn reads it.

**Why code-registered instead of `SKILL.md` loading.** The existing Agent Skill
for `winnie-index` is instructions for an external agent. Loading executable
tool bundles from arbitrary Markdown would add a discovery format, a trust
boundary and a second way to register tools before there is a second consumer.
The manifests are code-registered now; file-based skill discovery is a named
follow-up, not an accidental omission.

**Architecture test.** `winnie-chat` may be imported by `winnie-assistant` and
depends only on `winnie-core`; `winnie-local` remains the shared execution
layer. Neither is a capability, and neither imports the host or another
capability.

## App and console icons (2026-11-01)

**The mark is used as drawn; only the frame becomes a disc.** The woodpecker is
one even-odd path from the separate logo project, vendored verbatim, and the
icon pipeline places a paper disc on its own frame circle instead of redrawing
anything. The frame is a 0.62%-of-width hairline, i.e. 0.3 dp at 48 dp, so it
vanishes exactly where the icon has to work. Filling the frame's circle keeps
the bird-inscribed-in-a-circle composition and the mark's dark-on-light polarity
while giving an edge that survives 16–48 dp. Rejected: leaving the hairline
(the launcher showed a bird with no container), and thinning or thickening the
stroke by hand (that is redrawing the logo, and the two surfaces would drift).

**Navy and paper only, not v6's crimson.** The logo project pairs `#003087` with
`#C8102E` as a red disc inside a blue field with the black hairline between
them. At launcher size that hairline is a third of a pixel and reads as a smudge
between two saturated colours, so the icons use ink `#003087` on paper `#FFFFFF`
and the crimson survives only as a variant in the review sheet. A white bird
knocked out of navy was the other contender; it is bolder at 144 px, but the
circle disappears into the launcher mask and the eye and crest gap fill in first.
Details, geometry and the rejected alternatives: [brand.md](./brand.md).

**One composition for both surfaces.** The console's tab icon and sidebar brand
are the same paper disc and mark as the launcher's foreground; the launcher adds
the navy field behind it. That is why there is no light/dark icon pair: on a
light page the disc is invisible and the mark stands alone, on a dark one it is
a light badge.

**`<monochrome>` in the `v26` file, and no legacy PNGs.** The platform ignores an
unknown child of `<adaptive-icon>`, which is how Google's own template ships the
themed-icon layer, so a second `mipmap-anydpi-v33` file would only duplicate the
two real layers. `minSdk = 26` means the adaptive icon is always available, so
density-specific legacy mipmaps would never be loaded.

**Generated by a script, guarded by a test.** `tools/brand/build.py` emits the
vectors, the SVGs and the rasterised touch icon, with the mark's placement baked
into the path data so a `VectorDrawable` needs no group transform and no
`clip-path` guesswork. `LauncherIconTest` rasterises the compiled drawables under
Robolectric: this machine has no emulator, and a malformed icon fails silently
(the build stays green and the launcher falls back to a default), so the test is
the only thing that would notice.

## News is a capability built from a ported prototype (2026-11-01)

**Ported, not rewritten from the docs.** The pipeline started as the WLB
`tools/news` prototype: working collectors, a material contract, an event-card
aggregator and a documented six-stage design. The collectors were ported with
their tests; the stages were rewritten behind ports. Reimplementing RSS/Atom
parsing and the tophub JSONP handling from a description would have discarded
the only part that is genuinely fiddly.

**The schema is `winnie.news.*`, not `wlb.news.*`.** The old prefix names a
repository that no longer produces the data. A versioned contract is worth
keeping; a foreign namespace is not.

**LangGraph and LangChain are gone.** The prototype's seven-node graph is a
linear sequence with one branch (commit / verify / new). Plain async functions
express it with less state plumbing, and `langgraph` plus `langchain_openai`
would be two dependencies for control flow that the tests already pin. If a
future stage needs real fan-out with checkpointing, that trade can be revisited
on evidence.

**DeepSeek-specific code is gone.** The model is an `LLMPort` with a purpose;
the host resolves `news.extract`, `news.write` and `news.review` through the AI
registry on every call. Extraction, writing and review can therefore use
different models, and a rebind takes effect on the next run. A new registry
feature starts unbound; the console can create the binding, so the capability is
not permanently unusable without an implicit fake.

**The event store is a JSON file.** Recall is an in-memory inverted index
rebuilt on load. A personal deployment holds thousands of events; a file the
owner can read, copy and diff is worth more than a schema, and it avoids a
second database to keep in sync. A run is a directory of JSON plus Markdown, so
a stage can be replayed without the service.

**One search integration.** The search stage reuses `assistant.search` rather
than adding a second provider and key. Search results are leads: only URLs with
a usable snippet reach the writer, and the writer may cite only URLs that were
in its input.

**The assistant reaches news through a protocol.** `winnie-assistant` defines
`NewsGateway`; the host adapts `NewsService` to it. The alternative — importing
`winnie-news` from the assistant — would make two capabilities depend on each
other and would keep a news tool in the schema after the pipeline is disabled.
The price is one adapter file and an optional constructor argument.

**Publishing is out of scope.** The pipeline ends at a reviewed Markdown file.
The review verdict is a gate for a person, not permission to post: `passed`
means ready for human publication and `needs_human` means a decision is needed.
Publishing needs its own target, credentials and audit trail.

**The console shows Markdown source.** A rendered draft needs a Markdown
renderer plus a sanitizer, because the text is model output derived from
untrusted articles. Showing the source costs readability and closes an XSS
class; a sanitised renderer is a follow-up with its own tests.

## Weekly schedule tool (2026-11-01)

**Line splicing, not re-serialisation.** `packages/winnie-schedule` edits
`~/Projects/Mine/WLB/work/**/<week>.md` by replacing the exact lines an event
occupies. Rendering the parsed model back to Markdown would be shorter to write
and would quietly rewrite prose, blank lines, `\x0b` paste artifacts and badge
definitions in a file a human also edits. The parser therefore owns the file's
line list and the model is a view with line ranges; a corpus test asserts that
every week file parses and renders back to its original bytes, and that
add-then-delete returns the file unchanged.

**The package does not create week files.** The skeleton — calendar block,
routine items, badge definitions — comes from WLB `tools/weekly.py`, which also
creates month/quarter/year indexes and the `S_*` symlinks. A second generator
here would drift from it, and skipping weeks silently would be worse than a
clear "expected path, run the generator" error.

**Week files before 2024 are read-only.** They have no `### MM/DD` day sections,
so there is nowhere to anchor a day-scoped edit. The tool parses them, lists
their week-level items and refuses mutation with exit code 4 rather than
guessing which day an item belongs to.

**Disagreements are reported, not repaired.** A day heading that contradicts its
date, or an event whose `` `MM/DD` `` tag contradicts its section, is a human
mistake in the source: the parser emits a warning that `parse --json` carries,
because silently rewriting the tag would hide the fact that a line was pasted
into the wrong day.

**Standard library only.** The tool runs on the system `python3` with no
virtualenv, so an agent can invoke it from any directory with
`PYTHONPATH=.../src python3 -m winnie_schedule`. A scheduling helper that needs a
dependency resolved before it can answer "what is left today" is not a helper.

**The Skill is written in Chinese.** Every other document in this repository is
English, but this one describes a Chinese Markdown format and is read together
with the sibling `wlb-work-notes` skill while editing Chinese content; a
translated vocabulary ("day section", "week item") would have to be mapped back
to the headings in the file on every use. Code, comments and this README stay
English.

**Root resolution: flag, then environment, then the conventional path.** The
schedule is not inside this repository, so `--root` / `WINNIE_SCHEDULE_ROOT`
precede the default `~/Projects/Mine/WLB`, and a root without a `work/`
directory is an error instead of an empty result.

## Weather watch: deterministic, a skill, and honest about alerts (2026-11-01)

**No model in the loop.** The daily check fetches a forecast, applies fixed
rules and writes a notice. A model call would add cost, latency and a chance to
invent a temperature for a message the owner may act on. The model still has a
`weather_forecast` tool for conversational questions, and it reads the same
numbers and advice the watcher used, so the two cannot disagree.

**The feature is a skill.** `weather` is registered like `web` and `memory`, so
the console's existing Skills page is the feature list: disabling the row removes
the tool from the model and makes the watcher skip its next due check. A separate
features registry would have duplicated enable/disable, persistence, the API and
the page.

**Open-Meteo, and derived warnings.** It needs no key, returned Shanghai in
0.9 s, and covers the rain/snow/temperature rules directly. Official Chinese
alarm endpoints were tried: `weather.com.cn/data/alarm/...` and
`product.weather.com.cn/alarm/grepalarm_cn.php` answer `403`, and the commercial
providers need a key. The `severe` advisory therefore comes from WMO codes
(65/67/75/82/86/95–99) and the message says "留意官方预警" rather than claiming an
official warning. A real alert feed is an additive `alerts` provider, not a
rewrite.

**Notices are conversations, not chat turns.** A notice is created with
`source: weather`, `channel: weather` and an assistant message as the first
block: there is no user turn to fake, and the console can filter the channel.
Reusing the chat path would have required inventing a user message and would
have run the model.

**One advice per change per day.** The fingerprint is the date plus the sorted
advisory kinds. Without it, a daily check that finds the same rain would create a
notice every morning; with it, a genuinely new signal (a wind warning appearing
later) still gets through.

**A manual check does not move the schedule.** `POST /weather/check` is for the
console and for tests. If it advanced `next_run`, checking at 06:00 would cancel
that morning's scheduled notice. The watcher advances the schedule; the manual
path does not.

**Location and schedule are deployment configuration.** The console manages the
feature list, not every setting. Changing the city is a `config.yaml` change,
which keeps the first version to one location and avoids a settings UI that
would have to be designed, validated and migrated before the feature has a
second user.
