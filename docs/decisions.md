# Decisions

Architecture and dependency choices that are not obvious from the code. Each
entry records the alternative that was rejected and why.

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

## Assistant: files first, no orchestration framework yet (2026-11-01)

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

**Default model.** `assistant.model: fake` in `config.yaml`, matching the
meetings `translator`/`summarizer` fake defaults. A missing `WINNIE_LLM_API_KEY`
must not silently downgrade to a real-looking answer, and it must not stop the
gateway and meeting APIs from starting; the assistant routes return `503` with
the exact missing variable, which is what the chat page shows. Switching to the
real model is `assistant.model: ""` plus the key in the environment.

**Chat surface.** A self-contained page served at `/api/v1/assistant/ui` rather
than a panel in `apps/admin`. The console is a separate, still-uncommitted
deliverable, and the chat page must work on a phone over the LAN with no build
step. It stores the bearer token in `localStorage` and uses `textContent` for
rendering, so model output cannot inject markup.
