# Decisions

Architecture and dependency choices that are not obvious from the code. Each
entry records the alternative that was rejected and why.

## A check asks the supplier, and says what it did not prove (2026-11-02)

**Decision.** `POST /api/v1/ai/vendors/{id}/check` asks the supplier for its own
model list with the stored key (`GET {base}/models`, which costs no tokens) and
returns one of four verdicts: `ok` with the model count and any bound model the
list no longer contains, `unauthorized` for `401`/`403`, `error` for any other
status or an unreachable endpoint, and `unsupported` for a supplier that exposes
only speech. The call is read-only: it does not refresh the gateway route table,
does not write the registry, and the key never appears in the response. The
console offers it as **Check** on a supplier row, and the line it renders next to
an `ok` says that the key and the endpoint work, not that a completion succeeds.

**Why.** "Key set" was the only signal the page had, and it is not evidence. A
revoked key and a retired model both look configured until a real call fails —
in a chat turn, a meeting pipeline or a nightly job — and the error arrives far
from the console that could have caught it. `GET /models` uses exactly the
credential an internal call or the gateway sends, so it answers the two ways a
configured supplier goes stale at once, and reporting the missing bound models
from the same response is free: a catalog change otherwise reads as the
supplier's fault.

**Rejected: send a one-token chat completion instead.** It proves more — that
the model answers — but it spends money on every click and needs a model the
caller may not have bound yet; a supplier with 200 models would still have been
asked about one. The model list is the cheapest question that separates "the key
is rejected" from "the endpoint failed", which was the question nobody could
answer.

**Rejected: report a rejected key as a check failure.** A `401` from the
supplier is a *successful* check with an `unauthorized` verdict, so it is
`200 {"check": {...}}` and the console renders it as data. A check that cannot
be attempted at all — the supplier has no stored key — is `409`, because the
request itself was impossible.

**A speech-only supplier is `unsupported`, not an error.** Volcengine exposes an
ASR API and no model list. The console hides **Check** there and the row already
says why, while the endpoint still answers `unsupported` for a scripted caller
or a row rendered before the endpoint changed. An Anthropic supplier is probed
with `x-api-key` and `anthropic-version` instead of a bearer token, because
sending `Authorization` to Anthropic would report every working key as rejected.

## A supplier key is stored with its models, and the env file becomes a backup (2026-11-02)

**Decision.** Each supplier's API key is stored on its record in `data/ai.yaml`,
next to its model catalog, and every internal model call — assistant, meeting
translation, minutes, news, ASR — reads it from there. The console writes a copy
into `packages/winnie-llm/.env` after every save and keeps the file's comments
and unrelated lines; that file is a **backup**, not a source. `PUT
/api/v1/ai/vendors/{id}/key` is the only write path, the value is never returned
(the console sees `api_key_present` and the last four characters), and the file
stays mode `0600`.

**Why.** The previous rule split one concept across two stores: the console
managed the supplier while the key lived in a file the console could not write,
so saving a key meant editing `.env` by hand and restarting the API — while the
gateway, balances and internal jobs each re-derived the variable name. A key
whose value and record live in different places drifts: the record says the
supplier is configured, the file says it is not, and the first call fails with a
message about a variable. Storing the key with the model it pays for makes "is
this supplier usable" a single read, and lets a key change take effect on the
next call without a restart.

**Rejected: keep `.env` as the source and add a console editor for it.** The
console would still be editing a second document that the registry has to
re-read, and a running process would still not notice a change. The mirror is
one-way (registry → file), so there is exactly one writer and no reconciliation.

**Rejected: encrypt the key with a master key.** The key would protect against a
stolen `data/ai.yaml` only if the master key were stored somewhere else; on a
single-user host that means an environment variable and a key-rotation
migration, and the file is already `0600` inside the same trust boundary as
`data/gateway-keys.yaml` and the meeting database. Revisit if the registry is
ever served by a multi-tenant deployment.

**The env file is kept, unchanged in shape.** It still holds one annotated line
per supplier, so a human can read the state of the deployment, and a
gateway-only deployment (`ai.enabled: false`) still injects from it because it
has no registry. A key that predates this change is imported onto its supplier
once, on the first read of a `version: 6` file (or on a fresh seed), which is the
only remaining read of the file as a source. The process environment is no
longer consulted for internal calls at all.

**Verified.** `packages/winnie-llm/tests/test_registry.py` covers the one-time
import, the mirror in both directions of a save, clearing a key, and that no view
contains the value; `apps/api/tests/test_ai_admin.py` covers the console
endpoint, the write-only response, the `…last4` hint and a restart-free key
change in the route table.

## An ambiguous birthday is reminded twice, not refused (2026-11-02)

**Decision.** When one person has two dates in the synced data — `圆圆两岁生日`
on a one-off 12-12 and a yearly lunar `圆圆生日` on 12-20 — the planner schedules
**both** and adds one P2 conflict item that asks which is right. The answer is a
`prefer` entry in `proactive/subjects.yaml`, applied by the next scan.

**Why.** The plan said to refuse until the owner resolved it. That would have
left the motivating case silent: the owner asked where 圆圆's birthday was, and
a design that then says nothing until a question is answered is worse than a
reminder for a date that turns out to be the other celebration. A wrong-date
reminder is recoverable in one message; a missed birthday is not. The conflict
item makes the ambiguity visible instead of hiding it behind a choice the code
made silently.

**Rejected: pick the recurring row.** The one-off may be the real celebration
(the family may do both a solar and a lunar one), and the code cannot know.

**Rejected: block all stages until `prefer` exists.** It turns a question into a
precondition, and the question may sit unanswered for days.

**Also decided in the same slice.** The escalation ladder counts only sends
that reached a channel, skips a dead channel inside the same delivery, and is
bounded by `escalation.after_minutes`; after that an unacked item is marked
`digested` by the next digest rather than retried for ever. The digest itself
is a row (`digest:<date>:<slot>`), so it is idempotent per slot and visible in
the console like everything else.

**Verified.** `apps/api/tests/test_proactive.py` covers idempotent planning,
quiet hours, the daily budget, the ladder with a dead channel, the conflict
item, backlog collapse, the digest and the token acknowledgement. Against the
real database the engine planned 124 birthday stages, one conflict and one
overdue digest from data that already existed.

## A yearly event may carry its lunar date in the RRULE (2026-11-01)

**Decision.** `winnie_phone.recurrence` classifies a yearly rule before
expanding it. If `(BYMONTH, BYMONTHDAY)` equals the event's local start date,
the rule is Gregorian and `dateutil.rrule` expands it. If those fields equal the
**lunar** date of that start, the rule is a Chinese-calendar provider's private
encoding and is expanded through a lunar calendar. Anything else falls back to
RFC 5545 with a warning. Expansion is no longer "whatever `rrulestr` returns".

**Why.** The owner's phone stores lunar birthdays this way. Six of the ten lunar
rows came from an imported QQ calendar and four from the local provider, so it
is the convention of the ecosystem, not one app's bug. Read as Gregorian it is
silently wrong by up to five weeks:

| Event | `dateutil` | Truth |
|---|---|---|
| `圆圆生日` | 2026-11-12 | 2026-12-20 |
| `舅舅生日` | 2026-12-21 | 2027-01-28 |
| `老婆生日` | 2027-09-10 | 2027-10-10 |
| `农历月半（元宵节）` | 2028-01-15 | 2027-02-20 |

An RRULE that parses is not an RRULE that means what the parser thinks, and the
failure has no error path: the read returns a plausible date, the assistant
quotes it, and the owner acts on it.

**Rejected: display the provider's `Instances` instead of expanding.** The
phone can expand its own rules correctly, but a read would then depend on the
phone being online, and the same rows are read by the console, the export and
the proactive planner. The convention is decodable on the server and belongs
where the other date logic lives.

**Rejected: store the next occurrence as a column at sync time.** The phone's
`Events` row has no expanded occurrence; inventing one at sync would freeze a
horizon and go stale for a series longer than it.

**Rejected: ask the model to convert the date.** The date has to be right
without a model bound, and this is exactly the arithmetic models are worst at.

**Rejected: treat every yearly event whose rule does not match its start as
lunar.** `琪琪的生日` proves a Gregorian yearly birthday exists in the same
database. The classification needs both comparisons and a fallback, not a
heuristic on "mismatch".

**Library.** `borax` 4.1.3 (MIT, pure Python, no dependencies, released
2025-04) supplies both directions: `solar -> lunar` for the classifier and
`lunar -> solar` for the expansion. Rejected: `lunardate` and `zhdate`, both GPL
and therefore incompatible with this Apache-2.0 project; `cnlunar`, maintained
and MIT but solar-to-lunar only; `sxtwl`, accurate and BSD but a compiled wheel
whose manylinux builds are x86_64/i686 only, so an ARM deployment would need a
C++ toolchain. The conversion table covers 1900–2100, which is the whole domain
here.

**Consequence.** `phone_calendar`, the console calendar view and any future
detector get the right occurrence from one fix. The eleven real rows are the
regression test, and their expected dates were derived with borax directly and
cross-checked against `lunardate`, so the test is not the implementation
asserting itself.

## Proactive interventions are detected deterministically and delivered with a receipt (2026-11-01)

**Decision.** A new capability, `winnie-proactive`, owns detection, policy,
composition and delivery tracking. Detection is SQL and date arithmetic over
data the server already holds; the model is optional and only rewrites the
message. Delivery is an item whose lifecycle (`planned → sent → acked`, or
escalated into a digest) is stored, not a fire-and-forget push. The full design
is [proactive.md](./proactive.md).

**Why.** On 2026-11-01 the owner asked when 圆圆's birthday was, and the answer
was "not found" while the calendar held `圆圆生日` (lunar, 2026-12-20) and
`圆圆两岁生日` (2026-12-12). Three independent failures: no component looks at
birthdays at all; the answer was generated from a distilled memory of an earlier
failed lookup, with no tool call in the turn; and the one read that could have
found the row would have expanded it to 2026-11-12. A prompt cannot fix a
missing trigger, a negative result stored as knowledge, and a wrong recurrence
rule at once.

**Rejected: a scheduled task per birthday.** The task would still need the
horizon, the lunar expansion, the source merge, dedupe, quiet hours, escalation
and acknowledgement — everything except the sentence. It also spends a model
call to decide whether to speak, which is the part that must be reproducible.

**Rejected: let the model decide whether to interrupt.** The decision has to be
auditable ("why did you tell me this?") and cheap; a daily budget cannot be
enforced by a prompt. The composer may choose words; the policy chooses silence.

**Rejected: one watcher per kind, like weather and gold.** Two bespoke watchers
were affordable. Eight would reimplement dedupe, quiet hours, escalation and ack
eight times, and they would each need their own push wiring.

**Rejected: fold it into `winnie-assistant`.** The assistant package is already
the largest, the detectors concern the owner's data rather than chat, and the
dispatch loop must run when no model is configured. Small ports (`CalendarPort`,
`SchedulePort`, `ComposerPort`, `ChannelPort`) keep the boundary the other
capabilities already follow.

**Decided by the owner the same day.** Three channels — the self-hosted push,
ntfy and email — and standing approval for Winnie's own reminder actions: it
may create its own calendar reminders and alarms without per-item confirmation,
in a dedicated calendar, at most two a day, for P0/P1 items. The confirmation
gate stays on messages to other people and on irreversible actions, because a
wrong recipient or a deletion cannot be recalled; the scope and the reason are
in [proactive.md](./proactive.md) §6, the choices in §16.

## Four independent gates decide whether a memory reaches an answer (2026-11-01)

**Decision.** Five changes, each closing a gate that a wording-independent test
now pins:

1. `procedures` is pinned alongside `identity`, `preferences` and `constraints`,
   and the category — not the file's front matter — decides whether a category is
   pinned. Front matter can still add pinning to an unpinned category.
2. `MemoryStore.recall` scores the unpinned notes against the incoming message
   and adds the top six, with `path:line`, to the prompt for that turn.
3. `remember` and the distiller reindex the files they wrote through the
   daemon's new `POST /admin/index`, instead of waiting for the five-minute
   sweep. The sweep stays the backstop for hand edits and other writers.
4. A zero-result strict search is retried by the daemon as an OR of the query
   tokens (`{"loose": true}`, hits carry `"loose": true`). The assistant opts in;
   the daemon's default stays strict.
5. `weather_forecast` accepts `location`/`latitude`/`longitude`, geocoding a name
   with the same provider as the forecast; the configured location remains the
   default and the watcher's subject.

**Why.** On 2026-11-01 the owner asked for a default weather city to be
remembered, then asked twice for tomorrow's weather. Both answers came from the
configured 上海. The note had been written correctly — the reply had even said
"已记到本地记忆（procedures）" — so the write path was innocent. Each gate failed
on its own:

- only three of ten categories were in the prompt, and the rule was in a fourth;
- the only other read path was a search the model had to choose to run, whose
  CJK query is an adjacency phrase, so the owner's paraphrase returned "No local
  document matched that query" and the assistant reported there was no record;
- the index was up to 300 s behind the write, and the per-root `watch: true` was
  persisted but never read (there is no inotify code);
- and the tool being steered by the memory had no input for the place, so even
  the turn that read the rule forecast 上海 and said so.

**Follow-up the same day: recall reads the conversation, not just the last
message.** The owner's requirement is that *every* turn retrieves memory against
the context and the message. A recall keyed only on the current line fails the
most common shape of a chat: "明天有雨么？" → "那武汉呢？", where the follow-up
carries one place name and no subject. `recall(text, context=[...])` now scores
the notes against the message (a shared bigram or word is worth 6) and the recent
exchange (worth 2), with the admission bar lowered to a single context bigram
when the message contributed no match of its own. `recall_limit`, `recall_chars` and
`recall_context_messages` make the budget and the window deployment settings.
Rejected alternative: run `search_documents` over the index on every turn. It
would add a network hop and a 5 s timeout to the critical path of every reply,
could not see a note written in the previous second, and pulls the whole owner
document corpus into a prompt that asked for memory. The index stays the model's
tool for the wider corpus; recall stays in-process over `profile/`.

**The scheduled check resolves its place the same way the chat does.** One day
after the read-path fix, the 07:00 push still arrived as "天气提醒 · 上海" while
the chat answered from the phone's coordinates — the owner's rule was honoured by
the path with a model and ignored by the path that talks first. The watcher now
asks the phone for its most recent fix and uses it when it is newer than
`weather.phone_fix_max_age_minutes` (120), falling back to the configured
location otherwise, and the notice title, webhook body and state carry the place
the forecast was actually for.

Rejected: just change `weather.location` to 武汉. It fixes today's screenshot and
breaks the first trip: the push would confidently report a city the owner left.
Rejected: follow the phone unconditionally. A phone left on a desk reports last
evening's city, and the scheduled notice is the one message nobody is looking at
the screen for when it arrives. The age bound is what makes "coordinates when
available" mean *now*.

**Rejected: pin every category.** The digest is a fixed budget in every prompt;
pinning `episodes` and `people` would spend it on facts that matter to a handful
of questions and starve the rules that matter to all of them. Recall is the
cheaper way to widen the always-present set, and it costs one recent-message
match per turn instead of a bigger constant.

**Rejected: make the daemon's search loose by default.** `search()` is a public
contract used by the console, the MCP surface and tests; a query for text that
was removed must keep returning nothing (`test_a_rename_never_lands_on_a_
tombstoned_path`). The fallback is opt-in and labelled, so "related wording"
cannot be mistaken for "the document says this".

**Rejected: raise `scan.interval_seconds` to seconds instead of adding
`/admin/index`.** A shorter sweep still races the next question and pays the
whole metadata walk every interval; a writer that knows the path can say so for
free. `watch: true` in the config was left in place but is documented here as
inert until an inotify implementation exists — a configurability that does
nothing is a trap, and a comment claiming "inotify acceleration" is worse.

**Rejected: geocode inside the tool.** `WeatherService.geocode` keeps the
vendor call next to the forecast it feeds, so the assistant never learns a
geocoding URL and a future provider change is one module.

**Verified.** The daemon's `POST /admin/index` indexed a note written after its
last sweep and a paraphrase then found it as a loose hit, while a strict query
still returned nothing; `check(location="武汉")` returned a 武汉 forecast from the
live Open-Meteo while `check()` returned 上海; the assembled prompt for "明天有雨
么？" contains "默认查询武汉" and the itinerary note. `make index-test`
(92 passed, 1 skipped) and the API suite (385 passed) cover all five gates.

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
No navigation or DI framework: the signed-in shell has four tabs and at most one
overlay route, and a `rememberSaveable` enum plus four booleans expresses that
without a graph. A navigation library plus Hilt would add build plugins and a
second way to construct view models before deep links or a second module make
them pay for themselves. The view models that must survive a tab switch (chat,
account) are scoped to the activity and keyed by the signed-in user id, so two
accounts on one phone never share a conversation.

**Build.** Android Gradle Plugin 9.5.0 with its built-in Kotlin support,
Gradle 9.8.2, `compileSdk = 37`, `compileSdkMinor = 2`, `targetSdk = 37`,
`minSdk = 26`. The Kotlin version is pinned through a `buildscript` classpath
entry so built-in Kotlin, the Compose compiler plugin and the serialization
plugin all use 2.4.21 instead of AGP's older bundled Kotlin. The current
AndroidX artifacts (core 1.19.1, Compose BOM 2026.09.00, OkHttp 5.5.1) require
API 37; API 36 was rejected by the AAR metadata check.

**HTTP.** OkHttp 5.5.1 with hand-written `kotlinx.serialization` requests
instead of Retrofit. The client wraps roughly two dozen endpoints in one file;
Retrofit would restate the same DTOs behind an annotation layer without removing
any code, and this client is directly usable from a JVM integration test.
Revisit when retry/metrics interceptors or a plugin architecture appear.

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

**Remembered credentials.** The username and password of the last successful
sign-in are sealed with the same Keystore key and pre-fill the login form. The
**Remember username and password** checkbox is on for a fresh install, an
explicit opt-out deletes the pair, the Account tab can forget it again, and a
failed sign-in never overwrites the pair that used to work. Signing out clears
the token and the open conversation id but keeps the pair — that is what
"remember" means.

**Rejected: Android Autofill or Credential Manager.** Both store the password in
a system credential provider and need an app association with a publicly
reachable domain; a self-hosted Winnie on a LAN host has neither, and a
self-signed HTTPS host would still have to be registered with the provider. The
local encrypted pair works with any address the owner types.

**Rejected: plain `SharedPreferences`.** The invariant every password-saving app
shares is that a copy of the data directory must not reveal the password. The
Keystore key never leaves the device, so a copied file does not decrypt
elsewhere. The remaining exposure is a person who can unlock the phone and open
the app, which is exactly what the eye toggle and the opt-out are for.

**Markdown rendering.** `com.mikepenz:multiplatform-markdown-renderer` 0.46.0
with its `-m3` module, checked on Maven Central when it was added (released
2026-10-13) and built with Kotlin 2.4.21 / Compose 1.12.1, the same versions
this module uses. It parses JetBrains Markdown into Compose UI, so there is no
WebView, no JavaScript bridge and no second rendering engine. Its heading
defaults are article-scale (`##` is 45 sp), so chat maps them onto the app's title
styles; a table wider than a bubble scrolls horizontally, which is the library's
own answer. No image loader is added: assistant replies are text, and adding Coil
is a separate decision.

**Rejected: `com.github.jeziellago:compose-markdown`.** It is published only on
JitPack, not Maven Central, and it wraps Markwon in an `AndroidView`, so text
layout, theming and selection run in a second engine that does not follow
Material 3.

**Rejected: `com.halilibo.compose-richtext`.** Its `richtext-commonmark` is still
`1.0.0-alpha05`; an alpha renderer is not the newest *stable* option.

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
the HTTP, WAV, credential and assistant-contract code, Robolectric renders of
every screen (including PNG screenshots at 360×800 dp for a human to look at),
and a JVM integration test that drives the app's own client through the real
backend. A physical-device run with the real ASR key and the chat flow is still
required and is tracked in [todo](../docs/todo.md).

**Interface language.** English is the default resource set and Simplified
Chinese is a full translation (`values/`, `values-zh/`); every user-facing
string comes from a resource, and the app follows the system locale. On Android
13+ `android:localeConfig` adds Winnie to the system per-app language page; on
older versions the system locale is the only control. No in-app language
switcher is built: the platform already provides one for the versions that can
do it well, and pre-33 would need `AppCompatDelegate` (an AppCompat dependency
and theme) for a setting the system exposes anyway. `StringResourceParityTest`
keeps the two files in step, which matters because a missing key silently falls
back to English.

Data that leaves the app stays English on purpose: assistant replies, server
error details, command results reported to the server, and provider exception
text are records, not UI chrome. Translating them would put the phone's locale
into data the server and the model read.

**Sync cadence.** The client ticks every 5 seconds while its process is alive,
but a tick is a local change check, not an upload (see
[phone.md](phone.md#the-5-second-loop-and-change-detection)). Each scope has a
persisted, cheap token and is sent only when that token changed; snapshot kinds
also have their own slower check cadence, and content observers bypass it.

**Rejected: a 5-minute WorkManager periodic worker as the fast path.**
WorkManager refuses a period below 15 minutes, so "every 5 seconds" cannot be
expressed there at all. A foreground service dedicated to sync would give the
cadence in the background, but it would add a second permanent notification next
to the push service's, and 5-second polling for a week is not a battery trade
worth making silently. The loop rides the app process and the existing alert
service; a killed process falls back to the 15-minute floor, and the phone-data
screen says so.

**Rejected: polling the server every 5 seconds.** That would make "did anything
change?" a network round trip — 720 requests an hour with nothing to say. The
tokens move that question to the phone, and a content observer answers it from
the provider's own change notification. The only server round trip on an idle
phone is the pending-command poll, capped at one per 30 seconds.

**Rejected: a location fix on every tick.** A fix every 5 seconds is a GPS drain
that buys nothing for a personal assistant; location is sampled on a five-minute
bucket, which keeps a usable trail without waking the radio constantly.

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

**Superseded (2026-11-01): the reply is rendered as Markdown.** A file that is
readable as a record is not readable as a conversation: model replies arrive
with lists, fenced code, tables and emphasis, and `pre-line` showed all of it as
punctuation. The chat now passes replies through the same token walker the news
draft already uses, so there is no second renderer and no new dependency — and
no new injection surface either, since that design never builds an HTML string
(a hostile reply's `<script>` and `javascript:` link still render as text).
Chat adds three things to the shared renderer: `breaks`, so a single newline
stays a line (a draft is a document; a reply is typed like a message), a GFM
task-list checkbox, and tables in Bootstrap's `.table-responsive` so a wide
table scrolls inside the bubble instead of pushing the page sideways. Two more
rules keep the bubble readable: headings are sized relative to the bubble
rather than the page, and links and inline code take a colour that clears 4.5:1
on AdminLTE's `--bs-secondary-bg` bubble, where `--bs-link-color` and
`--bs-code-color` measure 3.8:1 because Bootstrap sizes them for a white page.
The owner's own turn is still shown as typed: a leading `#` or `-` there is a
keystroke, not markup, and reinterpreting it would hide what was sent. Images
stay dropped (a remote pixel would report the reader's address). No syntax
highlighter is added: a code block renders as monospace text and scrolls
horizontally, because a highlighter would be a second parser and a second
injection surface for content that is already readable.

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

**Keys moved out of the integration records (2026-11-01).** The variable name is
derived from the supplier id, so an endpoint stores neither the key nor its name;
a per-integration `api_key_env` was rejected because a typo became a runtime
"variable is empty", the console had to ask the owner to invent a name, and
capability packages had no way to know which environment the host would load.
The value lived in `packages/winnie-llm/.env` at the time; **superseded on
2026-11-02** by "A supplier key is stored with its models, and the env file
becomes a backup" above, which moved the value onto the supplier record and made
the file a one-way mirror. A key still never appears in an API response or in a
browser, and rotating it no longer needs a restart.

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
`gateway: false`. (The gateway later began injecting the supplier's server-side
key too — see "Gateway keys replace vendor-key passthrough" below — but the two
records stay separate, because a route and a feature binding are still different
roles.) A v1 file is migrated in place on the next read. The alternative —
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

**Balances use server-side keys only.** The gateway now injects the same
server-side supplier key, so a proxied call does consume the account shown here;
the balance query still runs once per supplier, because that is the account the
key belongs to. The ledger records each proxied call with the Winnie key that
made it. (Before the key rework the gateway forwarded the caller's vendor key and
this paragraph said the balance was invisible; see "Gateway keys replace
vendor-key passthrough" below.)

**Recording is best-effort.** A failed append is logged and swallowed; a
telemetry disk error must never turn a successful model call into an error. The
same rule covers the balance query: one provider's failure becomes an error on
its row, not a failed page.

**Usage is a tab of AI providers, not its own page.** The two pages answered one
question from one registry: which supplier serves a feature, and what that
supplier's calls cost. A separate navigation entry made the reader bounce
between the supplier list and the report, while the balance table (a property of
the supplier's key) could only be understood next to the supplier rows. The
console therefore has `Suppliers` and `LLM usage` tabs on `/ai`; the report is
mounted only when its tab is opened, so the registry page does not query the
ledger. The tab is a query parameter (`/ai?tab=usage`) rather than local state
so a link can point at the report, and `/usage` redirects there instead of
404ing an existing bookmark. The report itself is unchanged: same endpoints,
same numbers.

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

**Superseded (2026-11-01): the console renders the draft, without a sanitizer.**
`marked` is used only as a parser: its tokens are turned into React elements, so
there is no HTML string to sanitize and no `dangerouslySetInnerHTML` to get
wrong. Raw HTML blocks render as visible text, images are dropped (a remote
pixel would leak the reader's address), and `javascript:`/`data:` link targets
become plain text. The remaining cost is that unusual Markdown (references,
footnotes, embedded HTML) renders as text rather than richly; for a news draft
that is acceptable, and the alternative — an HTML string through DOMPurify —
adds a second dependency and a configuration whose failure mode is XSS.

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

## Phone data sync (2026-11-01)

**A capability package, not host code.** `packages/winnie-phone` owns the
schema, merge rules, queries and command queue. The host owns composition and
authentication, exactly as it does for meetings and news; the package does not
import the host or another capability. The assistant declares a
`PhoneGateway` protocol and the host adapts the service to it, so the assistant
still does not import `winnie-phone`.

**Four typed tables plus one signal table.** Calendar, calls, messages and
contacts are joined on and filtered by time, so each gets a real table with
indexes and a unique `(device_id, external_id)` constraint. Notifications,
location fixes and installed apps are read as "what happened recently" rather
than joined, so they share `phone_signals` with `kind`, `label`, `body` and a
JSON payload. That keeps a new ambient kind a client change instead of a
migration. A single generic table for all seven kinds was rejected because the
assistant's structured filters (direction, call duration, event window) would
become JSON predicates that SQLite cannot index.

**One kind per request, upsert, soft delete.** A batch covers one kind and at
most 200 items, so a large history is paged and a malformed item fails with a
kind-specific error. The key is `(device, external_id)`, which makes a retried
batch idempotent. Deletes are soft because a later full snapshot would
otherwise resurrect a record the phone removed. This is the same reasoning that
makes the assistant's conversations append-only: the server should converge on
what the phone says, not race it.

**Full snapshots with an optional window.** Calendar, contacts, apps and device
info can arrive as `mode=full`, meaning "this is everything" and anything
missing is deleted. A 12-month calendar sync must not delete older history, so
the request may carry `since`/`until`; the deletion diff is then limited to rows
whose start falls inside the window. Contacts and apps have no natural time, so
their full snapshots cover the whole kind. Messages and calls reject `full`
mode: a complete SMS history would not fit one request, and a paged "full"
snapshot would delete the pages it has not sent yet. Their deletions must be
reported explicitly, which is why the client currently leaves SMS deletions
unpropagated rather than guessing.

**Writes are deferred commands, not server-side writes.** The server cannot
reach the phone, and inventing a push channel (FCM, a persistent WebSocket)
would add a Google dependency and a service that must be reachable for a
personal assistant to change a calendar. Instead a write is a row the phone
claims on its next sync. The chat-level confirmation is the safety property:
a proposal is inert until the owner says yes, expires unconfirmed, and the
assistant is instructed never to claim it ran. The cost is latency — a write
waits for the next sync, up to the 30-minute interval. A lightweight command
poll is the recorded follow-up.

**The owner is resolved once, explicitly.** A device belongs to the account
that registered it, while the assistant is a single-owner service. With
accounts enabled, the adapter uses `phone.owner_user`, or the only active
account; with more than one account and no explicit owner it refuses and says
so. Silently picking one account's phone for every conversation would be the
wrong failure mode for SMS.

**Android: WorkManager 2.12.0, one periodic worker plus debounced one-shots.**
WorkManager 2.12.0 is the current stable release (2026-09-23). The separate
`work-runtime-ktx` artifact was merged into `work-runtime` in 2.10, so the
build depends on `androidx.work:work-runtime` and `CoroutineWorker` comes from
it; using the old KTX artifact would add a compatibility shim for no benefit.
Content observers on the SMS, call-log, calendar and contacts providers request
a one-shot sync with a three-second debounce, which makes new data visible
almost immediately while the app process is alive. They are not the safety net:
a process that Android kills stops observing, so the periodic worker is what
guarantees eventual upload. A foreground service was rejected because syncing
is not user-visible work and would need a persistent notification.

**Notifications go through a bounded file queue.** The listener callback must
not block on the network, and the service can be killed at any time, so
captured notifications are appended to a JSON-lines file in private storage and
uploaded later. The file keeps at most 2,000 entries. A crash between upload
and ack re-sends a few records; the server deduplicates by external id, which is
cheaper than an ack protocol with a second file.

**SMS and call-log permissions mean sideloading.** `READ_SMS`, `SEND_SMS` and
`READ_CALL_LOG` are grantable by the OS but restricted by Google Play for apps
that are not the default SMS handler or a declared core-function exception.
This is a personal, self-hosted client, so the build targets sideloading and the
consent screen says so. Becoming the default SMS app to satisfy Play would take
over the owner's messaging UI — an unacceptable trade for a feature that reads
their messages for an assistant.

**Excluded data is a decision, not an oversight.** MMS parts and media, call
recordings, clipboard, health data and per-app usage statistics are not
collected. Each is either a much larger privacy surface with little assistant
value or requires an access mode that is disproportionate here. Notifications
do contain one-time codes; that is why their default retention is 14 days while
SMS is kept for a year.

**Retention is per kind and enforced on sync.** There is no separate retention
job for a single-owner instance: every sync purges rows older than their
window. Ambient kinds age out quickly (notifications 14 days, location 30),
structured history is kept long (calendar and calls 730 days, SMS 365), and
contacts/apps are snapshots whose windows only prune tombstones. `0` disables
expiry for a kind. Wiping a device hard-deletes its records and sync states and
the client resets its cursors; revocation is sticky until an explicit restore,
so a phone cannot undo an administrative stop by re-registering.

**The console shows phone data but does not edit it.** `/phone` in `apps/admin`
is a read-and-operate view: devices, per-kind records with filters, cross-kind
search, the command queue and the revoke/restore/delete actions. Editing a synced
record was rejected because the phone is the writer: the next sync would either
overwrite the edit or need a command to apply it, and a console that silently
loses a change is worse than one that says it is read-only. Deleting is the
exception and is deliberately destructive: it is how the owner withdraws data
from a server, so it asks for confirmation and the phone uploads again
afterwards. The page uses the same bearer session and the same per-account
scoping as every other route; there is no separate admin credential.

**The data is stored in clear text.** The database has the same protection as
meeting transcripts: filesystem permissions. Encrypting only the phone tables
would suggest a security property the rest of the store does not have, and
field encryption would break the `LIKE` search the assistant depends on. The
honest position is documented in `docs/phone.md`: encrypt the volume, use TLS,
and choose a retention window you can live with. Device-level encryption or
SQLCipher is a recorded follow-up, not a claim.

## A tombstoned path owns its unique key, so the sweep resurrects it (2026-11-01)

**Decision.** `scan()` loads *every* row for a root, tombstones included, and
`UNIQUE(root_id, rel_path)` stays the one rule that decides who owns a path. A
file that reappears at a deleted path re-indexes that row in place
(`state='new'`, `deleted_at=NULL`, chunks rebuilt); a rename may only move a
live row into a path that holds no row at all, tombstone included; and the
end-of-sweep deletion pass skips rows already in `state='deleted'`. The contract
is written down in `docs/knowledge-index.md` under "One row per path, tombstones
included".

**Why.** The diff filtered `state != 'deleted'` when it built its map of existing
rows, so a tombstone was invisible to the sweep but still owned the unique key.
A file that came back — `Winnie/profile/preferences.md`, deleted by one sweep and
rewritten a few minutes later — went down the `row is None` branch and inserted,
and the build died with `IntegrityError: UNIQUE constraint failed:
docs.root_id, docs.rel_path`. The same filter made the rename `UPDATE` collide
when it moved a live row onto a tombstoned path. Both abort the whole sweep, so
one delete-then-restore left the index permanently unbuildable, and no amount of
re-running could get past it: the broken state was in the catalog, not in the
filesystem.

**Rejected: `INSERT ... ON CONFLICT DO UPDATE`.** It silences the insert, but the
row it overwrites is a tombstone whose chunks were purged on delete. The
`text_hash` shortcut in `_index_document` would then mark it `indexed` with zero
chunks — a document that reports itself indexed and can never be found. Resetting
the state to `new` is precisely what forces the re-chunk.

**Rejected: delete the tombstone and insert fresh.** Freeing the key works for
the insert but throws away the record that the path was ever indexed (`status`
counts it), and it does not help the rename case, where two live rows contend
for one key.

**Rejected: let tombstones witness renames through the inode map.** A tombstone's
inode belongs to a file that no longer exists, and inode numbers are reused, so a
brand-new document could be adopted as the "rename" of a deleted one. Only a live
row may witness a rename.

**Guard.** `packages/winnie-index/tests/test_index.py` covers the delete → restore
path, a rename onto a tombstoned path, and a second sweep proving tombstones are
not re-counted as deletions. The real catalog was repaired by the next sweep
(row 2006 back to `indexed` with 2 chunks), and the sweep after that was a no-op:
7423 unchanged, 0 added, 0 deleted.

## Push: a self-hosted socket, not FCM (2026-11-01)

**No vendor push service.** FCM would wake a sleeping phone with the least
battery cost, but it needs Google Play Services on the device, a Firebase
project and credentials on the server, and a copy of every notification inside
Google's infrastructure. Many China-market ROMs ship without GMS or throttle it,
and this client is sideloaded anyway. UnifiedPush/ntfy would avoid Google but
adds a second app, account and trust boundary. A WebSocket from a foreground
service keeps the channel between the owner's server and the owner's phone, and
needs nothing installed beyond the app. The accepted costs are a persistent
notification, some battery use, and no delivery while the process is
force-stopped or the phone is in deep Doze.

**The database row is the contract; the socket is an accelerator.** `publish`
inserts and commits before it fans out, so a phone that was offline receives the
reminder from the backlog on its next connect. The alternative — a fire-and-
forget socket write — would lose notifications on every reconnect and would make
"send" mean "sent if you were listening". The backlog is capped
(`push.max_backlog`) and the client continues from the newest event when it is
truncated.

**The cursor is a monotonic integer, not a timestamp.** `seq` is an
`INTEGER PRIMARY KEY AUTOINCREMENT`, the public id is a separate UUID, and a
client reconnects with the last seq it displayed. Timestamps can collide and
clock changes make ordering ambiguous; a sequence cannot. The client writes the
cursor before showing the notification, so a crash either repeats nothing or
repeats one event that the service ignores (`seq <= lastSeq`). Read state is not
tracked on the server: delivery and "a person saw it" are different questions,
and only the phone can answer the second.

**`sync` is a control event, not a notification.** Approving a phone command
publishes a device-targeted event that makes the phone run a data sync
immediately. This removes the up-to-30-minute wait for a confirmed calendar
change or SMS without a second command-polling endpoint. It carries no text, is
never shown, and expires after five minutes; a stale wake-up is noise.

**Foreground service type `specialUse`.** The standard types do not cover "a
self-hosted notification socket". `dataSync` is capped at six hours a day on
Android 15 and would silently stop delivering; `remoteMessaging` is for
messaging continuity and does not describe an assistant's reminders. `specialUse`
has no time cap, requires a manifest declaration and (for Play) a justification,
and is honest about being an uncategorised case. This build is sideloaded, so
the Play review question does not apply yet.

**Two notification channels.** The service notification must be persistent, so
it goes to a low-importance channel where it cannot make a sound. Reminders go to
a separate high-importance channel. Merging them would either silence the
reminders or let the service's own notification buzz. The user can tune or
silence each independently, which is the Android-native way to express the
choice.

**The assistant can notify, but not on its own initiative.** `notify_user` is a
tool with explicit instructions (notify for a requested reminder or a finished
result, never for every reply) and scheduled task results push automatically.
The alternative — silently pushing every tool or model output — would turn the
phone into a log viewer. A scheduled task push is opt-out through
`assistant.tasks.notify_results` because some tasks are bookkeeping.

**One process, one hub.** The fan-out lives in process memory. A deployment with
two uvicorn workers would deliver a publish only to the connections on the same
worker. A Redis pub/sub layer is the recorded fix; it is deliberately not built
yet, because the default deployment is a single process and adding Redis to the
critical path for a personal reminder is a worse trade than documenting the
constraint.

**Notifications can leak on a lock screen.** The alert channel shows the title
and body. The assistant instructions keep bodies short and free of credentials,
the owner can set the channel to hide sensitive content, and the docs say not to
ask for a notification containing a secret. A per-notification "include body"
flag was rejected as a setting nobody will remember to use.

**No delivery receipt.** The cursor proves the phone received an event, not that
a person read it. Building a receipt would mean another round trip and a
per-device read model for a single-user assistant; the value does not justify
it. This is recorded as a limit rather than implied by the feature.

## Gold watch: self-sampled history, deterministic alerts, app push (2026-11-01)

**A keyless feed with no history means we sample.** `api.gold-api.com/price/XAU`
returns one quote and needs no key; it has no candles. A periodic sampler with
the samples persisted in `.state/gold.json` was chosen over adding a market-data
dependency or a paid provider: the feature only needs tens of minutes of
history, and an explicit sample history is inspectable. The trade-off is that a
spike inside a five-minute interval is invisible, and the first alert needs the
process to have been up for the window.

**Deterministic, like the weather watch.** The watcher does not call a model. An
alert that may move money must not depend on a model being bound, and the body
has to be a price and a percentage, not a paragraph. The `gold_price` tool reads
the same service, so chat and the alert cannot disagree.

**Thresholds and cooldown.** 1 % against the sample closest to one hour ago, or
2.5 % over 24 hours, with a two-hour cooldown. The window baseline is labelled
with its actual age (a 45-minute move is not called an hour). A 24-hour rule
catches a slow grind that never trips the hourly one. The thresholds are
deployment configuration because this instance has one user; per-user settings
would need a console surface that does not exist.

**Push through the existing notifier.** The alert goes through the same
`Notifier` as `notify_user` and the scheduled-task briefs, so there is one push
path, one owner resolution and one backlog. The host resolves the owner (the
only active account, `cleanup`); the model never sees a user id. A conversation
record is written as well, because the push is a transient view and the record
must survive the message being dismissed.

**Manual checks never push.** `POST /assistant/gold/check` samples and reports
without delivering and without moving the schedule. A console refresh or a test
must not train the owner to ignore real alerts, and it must not consume the
cooldown.

**USD per troy ounce.** The source is a USD spot quote. A CNY-per-gram view
needs an FX rate and a unit conversion; showing an approximate number without
saying which rate was used would be worse than showing the source currency.

## Schedule in the console (2026-11-01)

**The console edits the Markdown files, and nothing shadows them.** A table of
events with a sync job back to `work/` was rejected: the owner edits those files
by hand and in git, so a mirror would be a second source of truth for the same
list, and every conflict would need a rule. `winnie-schedule` stays the only
writer; the API service adds the configured root, a lock and a payload.

**Every mutation answers with the refreshed week.** The alternative is a
separate `GET` after each write. Returning the week removes that round trip and,
more importantly, removes the temptation to compute the new event id on the
client: ids are positional (`202610-05-11#10/05#2`) and shift whenever an insert
or a delete happens above them in the same day.

**Events are addressed by `(week, id)` in the body or query string.** An id
contains `#` and `/`; percent-encoding it into a path segment works but reads
badly in logs and in a curl command, and the week has to be named anyway to pick
the file. `PATCH /schedule/events` and `DELETE /schedule/events?week=…&id=…` take
that pair instead.

**Administrator-only, unlike meetings.** The tree is not rows in the Winnie
database, it is a directory the service process can rewrite outside the
repository. An auth-disabled deployment still acts as a local administrator, so
the page works there; with accounts enabled, only an admin sees it.

**The page does not create week files.** Same boundary as the CLI: the skeleton
and the routine items belong to WLB `tools/weekly.py`. A week that does not exist
yet answers 404 with the path and the reason, which is what the page shows.

**Admin internationalization uses i18next + react-i18next.** The console is a
Vite/React SPA with no server rendering, so the language switch must be wholly
client-side and instant. `react-intl` is a credible alternative, but its
message-descriptor layer and ICU compilation add ceremony for a UI whose copy is
mostly plain labels; `i18next` + `react-i18next` is the smaller maintained
choice and supports the English string itself as the key (`keySeparator: false`).
That keeps the diff mechanical, makes a missing translation fall back to the
source copy instead of a key name, and needs no extraction build step. The
trade-off is weaker compile-time checking, so `npm run check:i18n` scans every
static `tr('…')` call against `zh-CN.json` and the build fails on a gap. The
choice of Chinese as the first translation follows the owner's language; the
supported list is explicit, not inferred from the request.

**Navigation groups express ownership, not visual taste.** The console has one
shell but two audiences: Workspace (Chat, Meetings, Phone data, Scheduled tasks,
Skills, API keys, Account) is the signed-in user's own data; Administration
(Dashboard, Schedule, Users, AI providers, Gateway routes, Document index, News)
is operator-only. One metadata table (`to`, `label`, `icon`, `group`,
`adminOnly`) drives the AdminLTE `nav-header` groups and the filter, so a page
cannot be visible but unguarded by accident. A section that is not a page of its
own — the LLM usage report inside AI providers — has no entry here, because a
menu of tabs would be a second navigation that can drift from the URL. The API is still the authority:
every Administration route has `admin_required`, and a direct URL renders a 403
panel. A normal user lands on `/chat` rather than the admin Dashboard at `/`;
the Dashboard is an operator overview, not a user home. A second console
application was rejected because it would duplicate auth, routing and the API
client for a small page set.

## Gateway keys replace vendor-key passthrough (2026-11-01)

**The old gateway was "transparent" only for the paths it had handlers for.** It
parsed the body, dispatched on a fixed path, and rebuilt the response. Measured
against a recording upstream, a request through it lost its query string
(`?api-version=…`), lost every header outside a five-name allowlist (so
`Idempotency-Key`, `X-Client-Trace` and the caller's `User-Agent` never arrived),
returned a 404 in FastAPI's `{"detail": …}` shape for `/v1/embeddings`,
`/v1/responses` and `messages/count_tokens`, and dropped upstream response
headers (`x-request-id`, `Retry-After`, processing-time). Each new endpoint
needed a new handler, which is the opposite of a proxy.

**The single `proxy_api_key` was unusable, not merely minimal.** Because the
gateway key and the vendor key shared the same two headers, no request could
satisfy both sides once the key was set: `Authorization: Bearer <vendor>` failed
the gateway check, `Authorization: Bearer <gateway>` was forwarded to the vendor
and failed there, and `x-api-key: <gateway>` with a vendor `Authorization` also
returned 401 because the check prefers `Authorization`. On an Anthropic route the
gateway secret was forwarded upstream verbatim. This was verified with a fake
upstream before any code changed.

**Forward bytes, do not speak the protocol.** The data plane is one catch-all
under `/{route}/{path:path}` that builds the upstream request from the client's
own method, raw path, raw query, bytes and (filtered) headers, then returns the
upstream's status, bytes and headers. Streaming is detected from the response
`Content-Type`, not from the body, and SSE frames are never re-serialised. The
router is registered last so `/api/v1/*`, `/routes` and the other host paths are
matched first; a small reserved-prefix list keeps a supplier endpoint from
shadowing them. The old per-endpoint handlers, protocol adapters and
`forward.py` are gone.

**What the proxy keeps is a tee, not a gate.** Usage extraction and the
ask/answer log observe the bytes on their way through; an unparseable body is
logged as raw text and a missing `usage` field simply means no tokens. Nothing
about observability can reject a call the vendor would accept.

**Callers present a Winnie key; the server holds the vendor key.** A key is
created in the console (`wsk_…`), shown once, and stored as a SHA-256 digest in
`data/gateway-keys.yaml` (mode `0600`). The route's supplier key is read from the
same `packages/winnie-llm/.env` the internal providers use and injected as
`Authorization: Bearer` (OpenAI) or `x-api-key` (Anthropic). The client's
credential headers are dropped, never forwarded. A route whose supplier key is
missing answers `503` naming the variable, instead of sending an unauthenticated
request upstream.

**Keys live in a file, like the AI registry.** A gateway-only deployment has no
accounts, no meetings and therefore no schema; a database table would make
console-created keys impossible there. The file is atomic, `0600`, and re-read
when its mtime changes, so one process sees another's write without a restart.
Revocation is a timestamp, not a delete: the ledger can still say which key made
an old call. A key may carry a route allowlist (empty = all), which turns a
copied phone or build-agent credential into a scoped one.

**`proxy_api_key` is removed rather than deprecated.** Keeping it would keep the
broken header collision alive and give two ways to authenticate. A stale
`proxy_api_key` in `config.yaml` now fails startup as an unknown key, which is a
one-line fix and impossible to miss. A gateway key in `local.yaml` was the only
alternative shape and was rejected because it is one more secret format to
document for the same single operator.

**Attribution is a field, not a new ledger.** `UsageEvent.detail` gains `key_id`
and `key_label`; the provider stays the route name. A first-class column would
have meant a schema migration and a new report dimension for a value that is
diagnostic, not billable. The consequence is explicit: there is no per-key
quota, spend cap or rate limit. A key identifies a caller and bounds where it may
go; the ledger reports what it used.

**Rejected: keep vendor-key passthrough and add `X-Winnie-Key`.** It would have
been smaller, but it leaves the caller holding a vendor secret, which is exactly
what the phone, the build agent and any future client must not do. It also keeps
two accounts in play and makes the console's provider balance silently wrong for
proxied traffic.

**Rejected: forward `Accept-Encoding` and pass compressed bytes.** A compressed
body cannot be parsed for usage, and a client's `br` would depend on a decoder
this process may not have. The request's `Accept-Encoding` is dropped, the body
is returned decoded with `Content-Encoding`/`Content-Length` removed, and the
bandwidth cost of one local hop is accepted.

## Gateway keys belong to the account that created them (2026-11-01)

**A key is a personal credential, so it lives on a personal page.** The first
slice put key management on the administrator-only Gateway routes page, which
made the operator the only person who could mint or revoke a client's key. A key
is per client (a laptop, a phone, a build agent), and the person who owns that
client is the one who knows when it leaked. The records gained `owner_id` and
`owner_name`, the list is scoped to the caller, and creation takes the owner from
the session. The page moved from Administration to Workspace and is visible to
every signed-in account; Gateway routes keeps the operator's view of the
upstreams and links to it.

**Revoking outside your scope answers 404, not 403.** The key id is eight hex
characters, so a 403/404 distinction lets a caller confirm that another
account's key exists. Both are refused; 404 is the one that reveals nothing.

**An administrator keeps an override.** `GET /keys?scope=all` lists every key and
`DELETE` accepts any key when the role is admin. That is the single action needed
when an account goes away. A key without an owner predates ownership; only an
administrator can see or revoke it, because there is no account to attribute it
to and hiding it would strand it.

**The trade-off is deliberate: every active account can spend the supplier
credit of the routes it may reach.** An empty allowlist means every route, and
there is no per-account route policy beyond the key's own list. Registration is
closed by default, so "an active account" is someone the operator created; the
framework already offers a single kill switch per account (deactivate, reset the
password, revoke the sessions). An admin-approval queue for keys was rejected
because it turns a personal tool into an approval workflow and still ends in the
same account-level control. An instance that opens registration must read
"anyone may sign up" as "anyone may call the models" and close registration or
scope the accounts; that consequence is stated in `docs/openai-proxy.md`.

**Ownerless records are optional fields, not a file version bump.** `owner_id`
and `owner_name` default to empty, so the existing file and any key created
before this change load unchanged. A version bump would have added a migration
for two fields that are display metadata.

## An event's indented block is its note, and it is Markdown (2026-11-01)

**The report.** A schedule view showed the 10/28 event as one line —
`所有更新都已经推送到两个平台了…` — while the file carries a paragraph, a
four-item summary and a closing sentence underneath it.

**The parser was not dropping it.** `event.lines` already held the whole block,
`event_end` already folded blank-line-separated indented paragraphs into the
event, and the corpus round trip proved the bytes were intact. What was missing
was a *field*: `to_dict(include_lines=False)`, which is what the console
consumes and what any JSON reader should use, carried only `text` (the checkbox
line). The block was reachable only through `lines`, so every consumer that did
not re-parse the raw file showed one line. A missing view is indistinguishable
from a lost value.

**`note` is that block, de-indented.** `Event.note` returns the continuation
lines as a Markdown string with the common indent removed and no leading or
trailing blank lines. The de-indent is not cosmetic: four leading spaces are
Markdown's code-block marker, so the raw block renders as a `<pre>`. The field
is in `to_dict` with or without `include_lines`, so the console gets it from the
payload it already fetches.

**Where it is rendered.** The console Schedule page renders it with the
existing safe `Markdown` component, so the summary keeps its numbered points,
links and code fences. `breaks` is on: the author writes one point per line and
only sometimes separates them with a blank line, so a newline must stay a
newline. The CLI has no renderer, so `show`/`list` print the block indented under
the event, and `--no-note` restores the one-line list for scripts. The name
follows the surface that already exists: `--note` appends exactly this block.

**Deliberately not done.** The event's first line stays plain text. It is edited
as one string, and pushing it through the block renderer would wrap it in a
`<p>` — or turn a leading `#` into a heading — inside a list item, for the sake
of `**bold**` on a template line.

**Guard.** `test_a_real_note_block_is_read_as_markdown` pins the reported 10/28
item (skipped when the WLB tree is absent), `test_blank_lines_inside_a_note_stay_paragraph_breaks`
pins the shape a fixture cannot express, and `test_week_returns_days_and_events`
asserts the API payload carries `note` and no raw `lines`.

## The schedule page is seven day tabs, and an event is a textarea (2026-11-01)

**Seven days, one tab each.** The week used to be a two-column card grid, with
one "add event" card above it and a `Day` select inside that card. Which day a
new event landed on was therefore a second thing to set after deciding what to
write, and the day grid competed for space with the week-level list. The tabs
make the day the page's primary axis: the tab *is* the date, so the select is
gone and the form inherits the day it sits in. The active tab resolves against
the loaded week (clicked date, then the anchor weekday, then the first day)
instead of being stored and re-synced, so navigating weeks lands on the same
weekday with no flash of an empty panel.

**Week-level items keep their own form.** They belong to `## 工作事项`, not to a
day, so no day tab can host them. Their form stays inside that section's card,
which is the only section `editor.add_event` writes week items to.

**A new event is a textarea, not an input.** The file format already supports a
note under an event, and `--note`/`editor._text_lines` indents a multi-line
value into exactly that block. A single-line input threw that away: the owner
had to add the first line from the console and then append the rest from a
terminal. The textarea sends `text` with real newlines, the server turns the
first line into the title and the rest into the note, and the panel renders the
note as Markdown. The edit form stays a single-line input on purpose: an update
rewrites the event's first line, so a newline there would corrupt the line
instead of adding a note.

**Drafts are not carried across tabs.** Only the active tab is mounted, matching
the AI providers page, so switching days discards a half-typed draft. Keeping
seven mounted forms to preserve a draft was rejected: the page's job is to read
a day, and the write path is quick to retype.

## Reverse geocoding on the server (2026-11-01)

**The server resolves an address; the phone only sends coordinates.** A device
that geocoded its own fixes would repeat the same lookup every sync (a phone on
a desk syncs without moving), spend battery and data on it, and need the third
party's key on the handset. Doing it on the server means one call per *place*
thanks to a coordinate cache, the key stays in the environment, and turning the
feature off leaves every row with its raw coordinates.

**Amap, behind a port.** Amap has the best address and POI coverage in mainland
China, and its regeo response carries what the request asked for: province, city,
district, township, street, and the nearest POI. A second provider is a class,
not a rewrite; `NullGeocoder` is the provider that is not configured and returns
nothing rather than failing.

**Photon is the default, and Amap is the opt-in (2026-11-01, later the same
day).** Amap gave the best answer and cost a registration: an account, a
Web-service key, and a coordinate datum to get right. The result was a feature
that ships switched off, so every location row read "not resolved" and the
coordinates went unread. komoot's Photon — a hosted index over OpenStreetMap —
answers the coarser question the owner actually asks ("which city, which
district, what is this place") with no key, no account and no quota, and the
console now shows a place by default. The trade is precision: Photon names the
nearest *named* feature, so a landmark 200 m away can be named while the fix is on
a road, and it cannot compose a postal address. Amap stays one config line away
for the street-level answer.

**WGS-84 in, GCJ-02 only where the provider speaks it.** Android reports WGS-84;
Amap answers in, and wants, GCJ-02, so `wgs84_to_gcj02` shifts the fix before the
request (measured ~555 m in Beijing, ~588 m in Wuhan). Sending raw WGS-84 to Amap
is not an error it can report — the coordinate is valid and simply resolves to a
real address on the wrong street. Photon is WGS-84 like the phone, so it needs no
shift at all, which is one fewer thing to get wrong. The shift is checked against
gcoord's independent implementation, and `geocode.coordinate_system` exists only
for a client that already reports the offset datum.

**A named feature further than 5 km is discarded.** Photon always returns its
nearest feature, and in open country that is a landuse polygon or a hamlet
kilometres away. Storing it would describe OpenStreetMap's coverage instead of
where the owner was, so past `MAX_FEATURE_KM` the fix keeps its coordinates.

**A provider's HTTP 200 is not a resolved address.** Amap rejects a key with
`{"status":"0","infocode":"10001"}` and HTTP 200, so a wrong key, a JS API key
sent to a Web service, an exhausted quota and a place nobody named all reached
the console as the same "not resolved". The provider's `status`/`infocode`/`info`
now go to the log on every empty answer; `SERVICE_NOT_AVAILABLE` (10002) is what
a key of the wrong type returns, and without it in the log it is
indistinguishable from a rural road.

**Verified against the owner's own key.** A key created years earlier still
works: the first probe on a real Wuhan fix answered `status=1` with a street
address, and all 34 stored fixes were re-resolved (`resolved=34
kept_previous=0`). The datum shift was confirmed the hard way on one coordinate —
30.437588,114.434242 became `天纵水晶郦都(北1门)` when converted and
`百捷国际广场`, a different compound, when sent raw. That deployment now names
`provider: amap` in its gitignored `config.local.yaml`, so the tracked config
still resolves something on a checkout with no key.

**The answer is cached in the database, not only in the process.** The provider
already memoised by rounded coordinate (~111 m), which keeps a phone on a desk to
one call — but that cache is memory, so every restart, every redeploy and every
one-off backfill run bought the same address again. A new fix now reuses the
address stored for a fix within 150 m, provided that address came from the
configured provider (otherwise switching providers would answer with the thing
that was replaced). The 150 m window is wider than the 111 m cell on purpose: a
fix rounding into the neighbouring cell is the same place. Rejected alternative:
a dedicated cache table plus a migration. The location rows *are* the cache —
they hold the coordinate, the address and its provider — and retention already
prunes them, so the extra table would add a schema, a migration and a second
source of truth for the same facts.

**The reuse window is 30 days of validity, not the newest N rows.** The first cut
looked at the most recent 200 fixes, which is a row count pretending to be a cache
policy: the same phone that syncs every five minutes would push a valid answer out
of the window in a day, while a phone that is idle for two months would keep an
answer far past any sensible life. `geocode.reuse_days` (30, matching the default
location retention) is the rule, and a row cap survives only as a memory safety
valve. Age is taken from the address's own `at` rather than the row's
`created_at`, because a reused answer copies that timestamp: measuring on the row
would let a chain of reuses reset the clock for ever, and the test asserts that an
address resolved 31 days ago is bought again even though the row carrying it is
two days old.

**`city` is filled from `province` when the provider reports none.** A
municipality (北京/上海/天津/重庆) sends an empty city, and the console's
"省 · 市 · 区" line must not show a hole where the city belongs.

**Geocoding never fails a sync.** A provider outage, a bad key or a timeout
leaves the address empty and the fix accepted: the coordinates are the data, the
address is a convenience. The reverse lookup therefore happens before the
database session opens, so a slow provider cannot hold a transaction.

**Old fixes need an explicit backfill.** Enriching on read would put a provider
call on the assistant's query path and on every console refresh; instead
`POST /locations/geocode` fills in what is missing, bounded by `limit`, and the
console exposes it only on the location tab. The alternative — silently
geocoding a year of history when the feature is switched on — spends the owner's
quota without being asked.

## SMS conversations are grouped by normalised digits (2026-11-01)

**The provider stores one person under two strings.** An incoming message often
arrives as `+8613800138000` while the reply to it is stored as `13800138000`.
A thread that matches the raw address therefore splits one conversation in two,
and a sender list built from raw addresses shows the same person twice. The
conversation view, the exclusion filters and the sender picker all compare
digits with the Chinese `+86` / `0086` prefix removed
(`winnie_phone.addresses.normalize_address`). The normalisation is applied to
SMS *query paths only*: calls and contacts keep the raw number the device
uploaded, because those rows are quoted back to the owner as they are.

**The filter hides noise; it does not select messages to keep.** The SMS list's
problem is the service numbers that write most often, not finding one of them.
An include filter was tried first and was the wrong control: the owner would
have had to enumerate the noise to remove it, and every new `106*` sender would
appear until selected. Pattern-matching two categories (`106`, five characters)
then turned out to be too narrow: operator extensions such as `1181142200013`,
`125200201211002424` and `10001885` are neither. The server therefore
classifies the address: a normal user number is a Chinese mobile (11 digits
starting `1[3-9]`), a Chinese landline (10–12 digits starting `0`) or an
international number; everything else is a service number. `+86…`, `0086…` and
`86…` are reduced to the national number first, and an address the rule cannot
place is treated as a service number — the list is noisy by default. An
explicit full number is matched anywhere so its `+86` spelling disappears too,
while a shorter fragment is a prefix: hiding `106` must not catch a mobile that
happens to contain `106` in the middle.

**Domestic and international are a stored-prefix distinction, not a country
lookup.** `+86`/`0086` is domestic, any other `+`/`00` prefix is international,
and a number stored without a prefix is domestic. That is enough to split the
list into the two views the owner asked for, and it needs no per-message carrier
or numbering-plan data. Two limits are worth stating. An international address
must fit E.164 (at most 15 digits after the prefix) and must not start with a
second zero: real data already contains bulk senders stored as `+106…`,
`+00…` or `0079…` with far too many digits, and those are domestic service
numbers, not an international conversation. And an international number stored
without its `+` cannot be distinguished from a Chinese one and shows as
domestic. The same predicate drives the origin selector, the service
classification and the senders picker, so all three agree.

**SQL narrows, the normalised comparison decides.** There is no generated
normalised column and no migration: the thread turns the number into its digits
and matches them with `ILIKE` (which also matches the `+86` spelling, because
the digits are contiguous inside it), then the service compares the normalised
form. The service/origin rules compile to `CASE`/`substr`/`length` expressions
that SQLite and PostgreSQL both evaluate. That keeps the schema unchanged and
costs one pass over the narrow candidate set. It would be the wrong shape for
millions of messages; this data set is one phone's history, and the list is
capped at 500.

**The filters and the picker are server-side.** The console shows at most 500
rows per request, so filtering a fetched page would leave a page that looks
empty while more messages exist outside the window — and a picker built from
the page would omit the number that only appears in older messages.
`/messages?hide_service_numbers=&origin=&exclude_addresses=&direction=` and
`/messages/senders` do the work over the whole window instead. `direction`
keeps the values the phone writes (`inbox`, `sent`); the console maps them to
Received/Sent, and "Sent" means the phone stored a sent message, not that the
carrier delivered it. Every exclusion is off by default: the list shows
everything until the owner asks it to hide something.

## The editor plugins became the news pipeline's prompts (2026-11-01)

**Two ChatGPT editor plugins are a contract, not a prompt to paste.** The
drafting plugin (about 200 characters, extendable to 300, `title` / `content` /
`tags`, a position kept intact, no source apparatus) and the hot-news plugin
(2 days, 30 items, four sections, three formats) describe what the owner wants
out of `winnie-news`. Shipping them as an assistant skill would have left two
writers disagreeing about the same batch. The rules therefore live in the writer
and reviewer prompts — quoted verbatim from the plugin `SKILL.md` files — and
every rule with a mechanical consequence is enforced in code: the generator owns
the shape, the review rules own the taste, the digest stage owns the batch view.
[docs/news.md](./news.md) maps rule to owner; a prompt edit alone can no longer
relax the contract.

**A quoted rule is the only rule.** The first cut of this change retold the two
plugins in English and added thresholds they do not contain — a 120-character
floor that belonged to nobody, and a 40-character headline limit presented as the
plugin's. That is how a contract drifts: every retelling adds an interpretation,
and the owner's own wording is lost. The prompts now load
`winnie_news/prompts/news_drafting.md` and `hot_news.md`, byte-identical copies
of the plugin files including their frontmatter, and `tests/test_skills.py` pins
both sha256 hashes so editing a quoted rule fails the suite. A constraint that
genuinely is not in a plugin — the 40-character headline cap, the
number-versus-material check, the sources trace — is labelled a pipeline rule in
code and docs, and never appears inside the quoted block. The prompt's only
additions are the JSON envelope, the field mapping and the batch settings; each
is introduced as pipeline integration, not as editorial guidance.

**The publishable text and the audit trail are different documents.** The
drafting contract forbids a source list or a link in the finished article. The
pipeline's whole value is that every sentence can be traced to an article. Both
are true because `sources` and `uncertainties` moved out of the text and into
the draft JSON, which review still requires to be complete and URL-valid. The
Markdown and the plain-text `title` / `content` / `tags` form carry no link at
all; a link in either fails review.

**The console shows the plugin's output, not a rendering of it.** The draft card
used to render the Markdown file, which is a console convenience and not what
the drafting plugin produces; the owner copies the publishable item out of the
page, so the card now shows the `title` / `content` / `tags` text verbatim and
offers a copy button for it, with a second button for the Markdown rendering.
The digest goes the other way: its standard and table formats *are* Markdown, so
the card renders them — literal `**` and pipes were neither the format nor
readable — while the copy button still writes the Markdown source. A pre-digest
run answers 404 and the card says 本次运行没有汇总. The copy path uses the
Clipboard API and reports when the origin is insecure and `navigator.clipboard`
is absent, instead of claiming a copy that did not happen.

**Tags, category and brief come from the writer, not from a second call.** A
digest needs one section and one scannable line per event, and the writer is
already reading the material with the tag taxonomy in front of it. Asking for
three more fields costs tokens, not a call. The generator normalises what it
gets (aliases, `#`, duplicate tags, the cap of seven) because those are shape
rules; the review keeps the judgement rules (`AI` not `人工智能`, no leader
names, no event-name tags) so the writer receives feedback it can act on.

**The plugin's numbers are the only numbers.** 内容规则 1 says 约200字,
extendable to 300字左右, and 内容规则 8 allows a shorter draft when the material
cannot support more. Review therefore notes a body under 200 and flags one over
300, quoting the rule's own wording in the detail; there is no floor invented
between them. The generator keeps its pre-existing 80-character sanity check so
nothing empty reaches review, and that check is labelled a pipeline rule.

**Only a passed review reaches the digest, and a short batch says so.** A
digest is what gets read; including a `needs_human` draft because it was recent
would bypass the review loop. Every excluded draft is counted by reason
(`needs_review`, `out_of_window`, `over_limit`) and the note states the real
count, because a silent shortfall looks like a broken source.

**The digest does not deduplicate by title.** Two items about one event mean the
aggregate stage failed to merge them, and that is information the owner should
see. A title-based dedupe in the renderer would hide an aggregation miss behind
a cleaner-looking briefing, and the fix belongs in the stage that owns the
comparison.

**The digest window is configuration, not a request parameter.** The run record
already carries what was fetched; a per-request window would either refetch or
silently filter drafts written under a different window. `digest.window_days`
and `digest.max_items` are set with the deployment, and the three formats are
rendered from the stored digest so a request costs no rebuild. A per-request
window is a follow-up that has to travel with the run.

## The wake word is a local model, and the turn after it is a paid call (2026-11-01)

**Decision.** "Winnie" is detected on the phone by a sherpa-onnx keyword-spotting
model (Apache-2.0) that runs offline in a `microphone` foreground service. The
utterance after the wake is uploaded to `POST /api/v1/assistant/transcribe` and
transcribed on the same ASR binding as meeting notes. Wake costs nothing per
use; the turn does.

**Why this split.** A cloud hotword cannot be zero-cost — it streams all day and
bills by the hour — and a press-to-talk recognizer is not hands-free. A local
keyword spotter is the only design that satisfies "say the word for free". The
follow-up utterance is a few seconds once per turn, so paying for it is
acceptable and keeps the transcript quality of the existing ASR.

**Why sherpa-onnx.** The keyword is a line of data, not a trained model: the
open-vocabulary spotter takes CMU phonemes from the model's own `en.phone`
(`W IH1 N IY0 @WINNIE`), so adding or renaming a phrase needs no training data,
no vendor account and no per-keyword licence. openWakeWord would have needed a
training pipeline per phrase; Porcupine is free only for non-commercial use and
proprietary; Vosk decodes continuously and costs more battery for the same task.
The bilingual zh-en model also leaves room for a Chinese keyword without a second
detector.

**Rejected: streaming the wake audio to the server and grepping the transcript.**
It is the cheapest thing to write, but it is exactly the design that cannot be
zero-cost and it sends every room sound to the server.

**Rejected: a `specialUse` service at boot that opens the microphone later.**
Android 15 forbids starting a `microphone` foreground service from
`BOOT_COMPLETED`. Working around it with a service type that does not describe
what the service does would hide a hot microphone; the app instead asks the owner
to open it once after a reboot.

**The AAR is fetched, the model is committed.** `sherpa-onnx-2.0.1.aar` is 47 MB
and is not on Maven Central, so `make android-kws` downloads it into
`apps/android/app/libs` (gitignored) and checks its byte count — a truncated
download still lists as an archive. The keyword model is ~5.3 MB of int8 ONNX and
is committed under `assets/kws`, so a build never needs the network for the
model and the exact weights that were validated are the ones in the APK. The
release tarball's fp32 encoder and test wavs are not committed.

**The pre-roll rides in the intent.** The detector needs ~320 ms of context and
the activity takes a moment to open, so two seconds of 16 kHz mono (64 KB) are
taken from a ring buffer and passed as an intent extra. Without it every turn
would start after the words that followed the wake word. 64 KB is well inside the
binder transaction limit, which is why the audio is not written to a file and
handed over by path.

**The service releases the microphone at the wake.** Android gives one capture at
a time; two `AudioRecord`s would leave one receiving silence. The detector stops
before the activity records and comes back when the turn ends (`ACTION_RESUME`)
or after 60 s, so an ignored wake does not silently disable listening.

**The keyword file carries pronunciation variants.** The model matches the exact
phoneme sequence it decodes, and a real recording of "Winnie the Pooh" decodes as
`W EH1 N IY0` while the dictionary form is `W IH1 N IY0`: a single-variant file
never fired for that voice. Four variants cover the measured case and the
plausible accents, and `numTrailingBlanks = 0` because the default of two blank
frames waits for a pause that "winnie, what is the weather" does not contain.
Measured on the desktop with the same sherpa-onnx release: the recording fires,
and the ten real recordings in the model's `test_wavs` produce no false hit.
Synthetic `espeak-ng` speech is out of domain — it fails the release's own
`LIGHT UP` keyword — so it is not a usable test signal.

**Not verified on a phone.** Wake rate, false-accept rate over hours of speech,
battery, microphone contention with another app and the Android 14/15
foreground-service behaviour are unmeasured: there is no device or emulator on
the development machine. The boosting score and threshold are the library
defaults until that measurement moves them; [docs/voice-wake.md](./voice-wake.md)
lists the five checks that would make this "validated" rather than "implemented".

## The console talks too: server ASR in, browser voices out (2026-11-01)

**Decision.** The admin console's chat page gets a voice conversation: the
microphone button starts a loop of listen → transcribe → send → speak the reply
→ listen again, and the composer becomes a status bar until it is stopped.
Audio goes **in** through the same `POST /api/v1/assistant/transcribe` endpoint
as the phone; speech comes **out** through the browser's own
`SpeechSynthesis`, with no new vendor, binding or bill.

**Same endpoint and same endpointing as the phone.** One ASR binding
(`meetings.transcribe`) transcribes both clients, so switching the supplier
changes both and every call lands in the usage ledger once. The console's
endpointer keeps `EndpointDetector`'s numbers — 20 ms frames, -38 dBFS floor,
1.2 s trailing silence, 6 s no-speech, 20 s maximum — because a voice turn
should not have two personalities depending on the device in the owner's hand.
The port is pure TypeScript, and the browser test drives it through the UI.

**PCM, not MediaRecorder.** The browser's recorder produces WebM/Opus;
recording-file ASR APIs are not reliably Opus decoders, and transcoding on the
server would add a dependency to save a few lines. An AudioWorklet hands blocks
to the page, an `OfflineAudioContext` resamples to 16 kHz, and the WAV header is
written by hand — the exact container the phone already sends, proven by the
same adapter.

**A stall guard the phone also has.** Endpointing counts audio, so a microphone
that stops delivering blocks would pin "Listening…" for ever. Three seconds
without a block, or 25 s wall-clock, ends the turn. The phone's
`ChatViewModel` already has this guard; the console needed it for the same
reason.

**Spoken replies stay local.** A vendor TTS binding would add a second billed
capability, a credential and a round-trip for something every operating system
already does. The reply is reduced to sentences first — code blocks are dropped,
links lose their URLs, tables are flattened — and the language is chosen from
the reply's script, not the UI's. The cost of this choice is real and accepted:
voice quality is the platform's, voices differ per machine, and Firefox reports
an empty `getVoices()` list while still speaking through the system
speech-dispatcher. An empty list is therefore not treated as "no speech
support"; only a failure to start is. A persisted "Read replies aloud" switch
exists because a quiet room and a meeting want opposite defaults, and Stop ends
the speech without ending the conversation.

**Auto-send, not dictation.** The transcript is sent as the turn immediately.
A voice conversation that needed a keypress per turn would not be a conversation;
the transcript appears in the message list, so the owner still sees exactly what
was sent, and a misrecognition is visible in the record rather than hidden in a
composer.

**Verified, and not.** A recorded Mandarin clip, injected as the browser's
microphone stream, reached the real ASR binding and came back as the expected
text; the console uploaded RIFF / mono / 16 kHz / 16-bit PCM; the reply was
queued for speech and the loop reopened the microphone; Cancel released every
track. The real microphone opens through the app's recorder. Permission denial,
a quiet turn and a stalled stream each produce their own message. The owner's
own voice, the recognition rate on it, and Safari/Chromium remain unmeasured.

## A whitelisted read neither asks nor needs the owner's yes (2026-11-02)

**Symptom.** The owner asked for tomorrow's hourly rain; the assistant answered
"我准备执行一条只读命令……回复「确认」我就执行". He had already told it, twice,
that weather queries need no confirmation — and the rule was genuinely stored and
pinned: `procedures.md` holds "获取天气预报信息（含逐小时降水数据）无需每次向
他确认，可直接执行只读查询", and `MemoryStore.digest()` puts exactly that line in
the system prompt of every turn.

**Cause.** The rule was in the prompt; the gate was in the code.
`LocalExecutor._command` put *every* `run_command` into `PendingActions`,
including whitelisted ones, and the pending text tells the model "ask the user to
reply with an explicit confirmation". The model could not obey the memory note:
`confirm_action` refuses a token created in the same turn, so the only path to
running the command was a new user message saying "确认". A prompt-level
instruction cannot override a code-level gate, which is why repeating it never
worked.

**Fix.** Two rules, one gate:
1. A whitelisted action is read-only by construction, so it runs immediately —
   no review, no confirmation. `local.whitelist.unattended: false` restores the
   old behaviour if an owner wants the confirmation for reads too.
2. `curl` joins the whitelist for a *read-only* GET only: `http(s)`, public host,
   no `-o`/`-O`/`-d`/`-F`/`-T`/`-H`/`-b`/`-u`/`-x`, no verb change. The
   classifier rejects loopback, private, link-local, reserved, credential-bearing
   and dotless-intranet URLs, so a "read-only fetch" cannot touch a local
   service. It is still a guardrail, not a sandbox: it cannot stop a request
   whose query string the model fills with data it already has.

**Rejected.** Teaching the model to say "已确认" itself: `confirm_action` compares
the *user* message, so self-confirmation is exactly what the turn boundary
exists to prevent. Also rejected: adding curl to `local.whitelist.commands` only
— that skips the reviewer but still pends, which is the bug that produced the
"回复确认" reply.

## A forecast that is fetched twice in a minute is a forecast fetched once (2026-11-02)

**Symptom.** "明天有雨么？" and "明天什么时间下雨？" 90 seconds apart produced two
full Open-Meteo round trips, and the second reply quoted a different temperature
  maximum than the first because the provider had rolled its model.

**Cause.** `WeatherService._fetch` did one HTTP request per call. The number is
deterministic and cheap, but nothing carried it across the two turns, and nothing
survives a restart.

**Decision.** Cache the parsed report in `.state/weather-cache.json` with a
30-minute TTL. The key is `coordinates|label|local date`, not the question text,
so a rephrase hits the same entry and a new day cannot reuse an old "today". An
entry carries its `fetched_at`, which the existing render already prints
("数据：Open-Meteo，11-02 23:01"), so a cached answer is honestly labelled rather
than passed off as live.

**Not done.** No stale-while-revalidate and no negative caching: a failed fetch
is still an error, and the cache is only consulted when the network was not. The
watcher and the tool share the entry, so a 06:50 question also warms the 07:00
check — acceptable because both want the same two-day window.

**Verification.** A live pair of checks: cold 1.43 s, second call 0.000 s with the
same `fetched_at` and the same message. Tests cover the TTL, the day rollover, a
different place, and a corrupt file being ignored.


## A spoken reply needs a provider-scoped voice and a dictated turn (2026-11-02)

**Decision.** Voice output becomes a first-class console feature rather than a
side effect of the voice loop: every assistant message has a play button, the
chat header owns a Voice / Rate / Preview menu, and the saved choice is
`{provider, voice, rate}`. A turn that came from speech carries
`input: "voice"` on `POST /assistant/chat`, and the host adds a concision
instruction to that turn's system prompt.

**Why per message, not only per conversation.** The voice loop already spoke the
reply, but that only helped while the loop was running: an old transcript, a
reply read after Cancel, or a typed turn had no audio at all. The play button
reuses the same Markdown-to-speech reduction as the loop and gives every message
a deterministic replay, which is also how a misheard sentence is checked.

**Why the voice identity is provider-scoped.** A voice name is not unique across
engines, and the two catalogs that exist today are qualitatively different: a
browser voice is device-local (`voiceURI`, present only on the machine that
enumerated it) while a supplier voice will be a server-side catalog entry. A bare
name saved today would be ambiguous the moment a second provider exists, and a
name saved on the desktop would be sent to a vendor that has never heard of it.
`{provider, voice}` makes the future binding additive: a new provider is a new
value plus its own catalog, and a missing browser voice falls back to automatic
by language instead of going silent. The setting stays in the browser because a
browser voice is meaningless on another device; a server-side preference would
be wrong for the only provider that exists.

**Why the picker is bounded.** Edge/Linux reports 321 voices (14 Chinese online
ones). Firefox's speech-dispatcher backend reports **14,805**, 945 of them
English and none tagged `zh`, because it expands every espeak language into all
of its variants. A `<select>` with 14,805 options froze the page in testing. The
picker therefore keeps up to 50 voices for the console's languages (English and
Chinese) and one representative for every other language, and always includes
the saved voice. Automatic selection is untouched: `utterance.lang` still gets a
voice for whatever language the reply is written in. The alternative — a
searchable combobox over 14k entries — is more UI than this one-owner console
needs, and the cap is reversible when a curated catalog (a vendor binding)
replaces speech-dispatcher.

**Why a dictated turn is marked.** The server cannot infer modality: the
transcript is ordinary text, and a phone turn and a typed turn are identical on
the wire. Without the marker the model writes a document for a question the
owner asked by voice, and a document is unlistenable. The prompt instruction is
per-turn and deliberately does not change the conversation file: the user and
assistant blocks stay the same Markdown, so history, search and the phone keep
working. The phone sends the same field from its wake turn.

**Rejected.** A vendor TTS binding now (a second bill, credential and catalog for
something the browser already does, with Edge's online voices as the quality
baseline); a global "always speak" switch (unexpected audio for typed chat, and
the voice loop's own switch already covers the conversation case); storing the
voice server-side (browser voices are machine-local); persisting the modality in
the transcript (a file-format change with no reader that needs it).

**Verified.** Firefox: a dictated turn reached the API as `input: "voice"`; the
play button spoke the reply and a second click stopped it; the menu showed 169
bounded options with automatic and four rates; Preview spoke. Edge: 321 voices,
14 Chinese, choosing 晓北 persisted `{provider: "browser", voice: …}` and the
Chinese preview used it and was audible (sink peak RMS 5,658). The prompt change
is covered by a unit test on `build_system_prompt` and the wire field by an API
test (`input: "voice"` accepted, an unknown modality is a 422).

## The phone speaks with its own engine, and pauses the microphone to do it (2026-11-03)

**Decision.** The Android client reads replies with the platform's
`android.speech.tts.TextToSpeech`, auto-speaks only a dictated turn's reply, and
puts the on/off switch plus four rates in Account → Voice output. It does not
offer a voice picker. While the phone speaks, the wake-word service pauses and
resumes when it finishes.

**Why the platform engine.** The console's reasoning applies unchanged: the paid
call is one ASR call per turn, voice *output* does not need to leave the device,
and every Android phone already ships a synthesizer that works offline. A vendor
TTS binding would be a second bill and a second credential for quality the
console cannot beat with Edge's online voices either. The engine's own limits are
accepted: which languages it can speak depends on what the ROM installed, and a
missing one is reported to the owner instead of failing silently.

**Why no in-app voice picker.** Android already owns this choice in
`Settings > Accessibility > Text-to-speech output`, including per-language
defaults, engine downloads and a preview. A second picker inside the app would
let the two disagree about which voice an answer is spoken in — the app would win
for its own window and lose everywhere the engine is used by other apps. What the
app does own is whether to speak at all and how fast, because those are
conversation-level choices the system cannot know. A saved `voice` field remains
in the settings structure, so a name pinned by a future build (or a supplier
provider, which *would* need its own picker) is honoured when the engine reports
it and ignored when it does not.

**Why only a dictated turn is spoken, and why the switch is not in the chat.**
The console's "Read replies aloud" switch lives inside the voice bar, because
there it only controls the loop; the loop itself is the decision to listen. The
phone has no bar to hang it on — a wake turn is a normal chat turn — so the
switch moves to Account → Voice output and defaults to on. A wake turn asked out
loud gets an answer out loud; a typed turn stays silent because the owner is
looking at the screen, and every reply keeps a speaker button for replay.
Speaking typed chats by default would make the phone talk in a meeting for a
message that was deliberately typed.

**Why the microphone pauses.** A spoken answer goes out of the loudspeaker and
comes back through the microphone. The assistant's replies are text about the
owner's day, and "Winnie" can appear in them; the detector would then start a
turn from the phone's own voice, with the answer's tail transcribed as a new
question. The console's equivalent is that its recorder is closed while
`SpeechSynthesis` speaks. On Android the detector lives in a foreground service,
so the hand-over is an `ACTION_PAUSE` that stops the engine but keeps the
service, the notification and the setting alive, and `ACTION_RESUME` when
speech ends. Pausing costs nothing when the loudspeaker is not used.

**Rejected.** A vendor TTS binding (same reasons as the console); a per-message
engine selector (the system screen owns it); streaming the reply into speech
sentence by sentence while the answer is still being generated (replies arrive as
one response today — the streaming work in `docs/plan.md` comes first); keeping
the detector listening while speaking and relying on the keyword score (the
reply is full-band, loud and close, which is exactly what the detector is tuned
to accept).

**Verified.** 168 JVM tests pass, including the Kotlin Markdown reduction
(identical rules to `speech.ts`, with the table/URL/code cases), the settings
round trip and rate clamp, the speaker button on a reply (one button, callback
with the message text, a stop control while that reply is read), the Account
card's switch/rate/preview, and the "no engine" path: an engine that never
answers produces the localized message and `speaking == false` instead of a
silent crash. Layout screenshots reviewed in both locales. The APK builds. **Not
verified:** speech on real hardware — no phone or emulator is attached to this
machine — so the engine's voice choice, its Chinese pronunciation, and the pause
actually silencing its own voice are device checks, listed in
`docs/voice-wake.md`.

## The self-repair worker is an out-of-process `dsh` subprocess (2026-11-03)

**Decision.** Code-level self-repair, when it is built, runs as one pinned `dsh`
subprocess per incident (`--profile headless --json` first, `--profile acp` when
a repair needs cancellation or progress), started with its working directory set
to a `git worktree` on a `fix/<id>` branch. Winnie owns the incident record, the
reproduction, the verification, the budget and the approval gate; the harness
owns only the model↔tool loop. The full design and its phases are in
[self-repair.md](./self-repair.md).

**Why out of process.** Winnie is Python and the harness is Node; there is no
supported in-process binding, and a sidecar library would put the harness's
lifecycle, crash domain and version inside the API the owner is talking to. A
subprocess is a narrower contract than a library: argv, stdin, newline-delimited
events on stdout, exit code.

**Why the loop stays ours.** No harness supplies the parts that make the
difference between repair and damage: reproduce before editing, isolate in a
worktree, treat the repo's own check as the definition of "fixed", cap the
budget, and never apply a diff without the owner. Those are code paths, not
prompt instructions — the same rule the `LocalExecutor` confirmation gate
already follows.

**Rejected.** Letting `LocalExecutor` repair code inline (no reproduction, no
test gate, no worktree, no reviewable diff); a SaaS autofix service (it cannot
see this repo's tests, services or deploy, and moves incident data off the
host); building our own model↔tool loop (a solved problem with a mature
harness already installed).

**Pinned and provisional.** `dsh` is `0.2.0-rc.2`, a prerelease, so the
dependency is the exact version and the subprocess contract, never its plugin
tree. Note that PyPI `deepseek-harness` is an unrelated third-party client;
nothing here depends on that name.

**Verified.** `dsh --profile headless --json` runs a one-shot task and emits
`session`/`status`/`thinking`/`text`/`final`; its `write` and `read` tools act
inside the invoking directory; a failing command surfaces `[exit code: 3]` in
the `tool_result`. Commands and output are in
[self-repair.md § 11](./self-repair.md#11-verification-log).

## Creating a calendar event is submitted, not proposed (2026-11-03)

**Decision.** `phone_calendar_create` now queues and approves the command in the
same turn. The owner is told what was submitted — title, local start and end
time, place — and corrects it in the next message if it is wrong. Changing or
deleting an event, and sending SMS, still wait for an explicit "确认".

**Why.** The confirmation was being spent on the one write that a second message
can undo. The owner's own words: 不用我回复确认，直接提交，然后告诉我日程是什么样子，
我发现不对，再沟通修改. A proposal costs a full round trip on every correct
event and buys nothing on the wrong one — the correction path (update/delete)
still exists and still asks.

**Where the policy lives.** `winnie_assistant.phone_tools.UNATTENDED_WRITES`,
a module constant checked by `_phone_propose`, which is the function that talks
to the gateway. Not in the prompt: the skill instructions and the tool schema
were rewritten to *describe* the new behaviour, but the code is what decides,
and a test (`test_creating_an_event_is_submitted_without_asking`) fails if the
gate comes back.

**Consequences.** Approval is still not execution — the phone writes the event on
its next sync (seconds with a push wake, otherwise the periodic interval), which
every tool output still says. The proposal-then-approve row remains in the
audit trail, so the console shows the same two steps; only the conversational
turn between them is gone.

**Rejected.** Removing the gate for every phone write: `sms.send` leaves the
phone and cannot be taken back, and `calendar.delete` destroys data the owner
did not mention in that message. Also rejected: a config key per kind — one
constant is easier to audit than a YAML list that can silently disagree with
the tool schema.

## An unattended memory pass needs a checked contract, not a careful prompt (2026-11-03)

**Decision.** The memory notes get a nightly pass — `MemoryTidy`, scheduled at
`memory.tidy.schedule` (default `daily 04:00`) — that merges notes saying the
same thing and retires notes that are dead. The model proposes; the code
decides, and every change has to pass a deterministic check before it is
written.

**Why a built-in pass, not a scheduled task.** The owner's ask was "Winnie
should tidy its memory on its own, not me". A conversation-created task runs as
a normal chat turn, and a chat turn cannot write `profile/`: the file tools
protect it (`_build_workspace` lists `root / "profile"` in `protected`). It
would also be non-deterministic — the same six bullets rewritten wherever the
model felt like it. The pass reads and writes the notes through `MemoryStore`,
the one component that already knows the file format.

**Why the guards.** A prompt that says "keep every detail" is a wish. The
2026-11-02 cleanup showed what the model actually produces: a merged rule that
sometimes loses a detail, and a "duplicate" that was really a correction. So a
merge must overlap every note it replaces (`dice >= min_member_similarity`), a
retirement must quote the entry verbatim (`evidence` is checked as a substring),
a `duplicate`/`superseded` retirement must name the surviving entry and be
similar to it, and the rendered file is re-parsed before it is written — every
surviving note must be present, in order, word for word. Any of those failing
means no change, not a warning in the log.

**Why a candidate scan first.** Sending ten files a night to be told "nothing to
do" wastes money and invites a model to invent work. Pairs are compared locally
by CJK bigram overlap and punctuation-insensitive identity, so a clean memory
costs no merge call, and an unchanged file is not swept again for `resweep_days`.

**Why 04:00 and no push.** The pass writes files and leaves a notice in
`conversations/`; it does not need the owner awake, and a 04:00 push would wake
him for housekeeping. `notify: true` exists for an owner who moves the schedule
to a waking hour.

**Where it lives.** `winnie_assistant.memory_tidy`; config under
`assistant.memory.tidy`; `GET`/`POST /assistant/memory/tidy`. Every run copies
the pre-image of each touched file and its plan to
`.state/memory-backups/<run>/`, keeping `keep_backups` (20) runs, so a bad merge
is a `cp` away from undone.

**Consequences.** A note can disappear without the owner asking — that is the
feature — but only when the model can quote the words that justify it, the note
is at least a week old, and the notice names it with the reason. A contradiction
that spans two files beyond the similar pair the pass can see is still a
conversation, not a sweep: the pass merges and retires, it does not re-interview
the owner.

**Rejected.** Letting the model rewrite whole files — the file then becomes a
model's summary of a model's summary. Dropping "obsolete" notes without a quote,
because that is exactly the operation that loses facts. Running after every chat
turn instead of nightly: the owner wants to read one notice, not forty. And a
prompt-only rule in `procedures.md` telling the assistant to keep its own memory
tidy — the same kind of note that failed to stop the weather bug: prompt text is
not a mechanism.

## Replies stream as the model writes them (2026-11-03)

**Decision.** `POST /assistant/chat` gains `"stream": true`: the same route, the
same turn, the same stored conversation, delivered as server-sent events. The
console and the phone page both read it. The plain JSON path is unchanged, so
every existing client, the scheduler and the tests keep working.

**Why.** A reply that takes six seconds to write is six seconds of a spinner.
The model already produces text progressively; the only thing standing between
that and the owner's screen was our own request handling. The browser also gets
something it could not have before: the text arrives while the turn is still
running, so reading aloud can start on the first sentence instead of the last.

**How.** Three layers, each additive:

1. `ChatClient.stream` posts with `stream: true`, parses the vendor's SSE, joins
   tool-call argument fragments, and ends with the same `ChatResult` the
   non-streaming call returns. Usage, the replayed assistant message and the
   ledger therefore have one implementation, not two.
2. `ChatEngine.run_stream` is `run` with the model round replaced by a streamed
   one. The tool budget, argument parsing and error mapping are the same code.
3. The route writes events (`start`, `token`, `tool`, `done`, `error`) and keeps
   `X-Accel-Buffering: no`, because nginx would otherwise hold every token until
   the turn ended, which is the bug this feature exists to remove.

**Deliberate asymmetry.** Anything that can still be an HTTP status code — 404
for an unknown conversation, 409 for a read-only task run — is decided *before*
the response starts (`AssistantAgent.open`), so a client never has to parse a
body to learn that the request failed. A failure after the headers went out
becomes an `error` event, and the assistant message is stored only on `done`, so
a stream that dies mid-sentence leaves no half reply in the transcript.

**Cost.** No retry mid-stream: replaying text the owner has already read is worse
than failing the turn. A vendor error after the first token is reported and the
turn ends; the next message starts a new one.

**Not done yet.** Speaking a reply sentence by sentence while it is being written
needs a speech queue that does not cancel itself between chunks; the console
still speaks the finished reply. Recorded in `docs/assistant.md` as the remaining
follow-up.

## A model name is a platform promise, not a supplier's id (2026-11-03)

**Decision.** The gateway now resolves every call through the registry's
**virtual models**, which become the platform's model catalog. One row publishes
a name (`deepseek-flash`), the other spellings it answers to
(`deepseek-v4-flash`, `deepseek-v4.1-flash`) and where it is served (supplier +
upstream model id). A client that sends a name the catalog does not publish is
refused with `400` before a byte leaves the process; the message lists what is
published. The body is re-serialised only to replace that one `model` field.

**Why.** Suppliers rename models between releases, clients keep sending the old
name for months, and a key holder must not be able to reach a supplier model the
platform never published (it would be billed to us and invisible in the catalog).
Naming had to become one place with two independent questions answered
separately: *is this name ours?* and *where does it go?* Aliases answer the first,
the target the second, and a per-key override changes only the second. Splitting
them is what makes "move every caller to the new model" one edit that does not
rename anything a client sends.

**Precedence.** A name resolves as: key override (if the key has one for that
name) → platform target from the catalog. There is no user or group layer yet.
The gateway key store has `owner_id`, but the platform has no group object and
no per-user model policy, so a "user override" would today be identical to a key
override for every key that user holds. Adding a layer that cannot differ from
the one below it is worse than not having it: it invites an operator to set a
group override and expect it to apply. When accounts get a shared object, the
layer goes *above* the key (user → group → key), because the more specific
setting must win.

**Where it lives.** The catalog is the registry (`data/ai.yaml`), edited in the
console under *AI suppliers → Virtual models*; keys and their overrides are in
`data/gateway-keys.yaml`. The gateway receives both as plain data on
`app.state` (`model_catalog`, `vendor_routes`) rebuilt from one registry read, so
a console edit takes effect on the next call. A gateway-only deployment
(`ai.enabled: false`) has no catalog and forwards the name unchanged — the
behaviour every earlier release had, and the reason the gate is "catalog is
empty", not "model is unknown".

**Rejected.** Passing unknown names through to the supplier (the old behaviour):
it makes the catalog advisory and the bill unpredictable. A per-key allowlist of
names *in addition to* overrides: the override already names a published model,
so a separate list would be a second, silently divergent gate. Putting the
mapping in `config.yaml`: that file is the gateway-only fallback and the console
cannot edit it, so the mapping would be invisible exactly where an operator looks
first.

## Gateway data plane moves to new-api (2026-11-03, deferred)

**Decision.** The model data plane will be new-api, not the in-process proxy.
Clients call `/v1` on new-api with Winnie-issued tokens (`sk-...`); Winnie keeps
the accounts, the console and the client-facing key records, and manages
new-api's channels, models and tokens through its admin API with a scoped
personal access token. new-api runs on SQLite. The channel page covers the
common fields only. There is no billing module: unlimited tokens plus a large
service-user quota and `LogConsumeEnabled=false` make the quota non-blocking,
not absent.

**Why.** The owner wants new-api's model→channel routing, protocol conversion
and channel features without a second place to administer. The in-process
gateway's byte-level transparency is deliberately given up: a fixed path list
instead of arbitrary paths, modelled headers instead of pass-through, new-api's
logs instead of the local ask/answer pair.

**What this costs.** Existing Winnie keys cannot be converted — the key file
stores only digests — so every client key must be reissued. Per-key model
overrides have no new-api equivalent and are dropped. Virtual models must be
re-expressed as channel model lists plus `model_mapping`, or retired. Winnie no
longer records gateway traffic in its own usage ledger.

**Why deferred.** It replaces a working component and loses parity in exchange
for aggregation, while the current phase is the knowledge base. The executable
plan, phases and acceptance criteria are in
[newapi-gateway.md](./newapi-gateway.md). Backlog: P4 in [todo.md](./todo.md).

## Error collection is host-owned, and the supervisor writes a spool (2026-11-03)

**Decision.** Failures are collected inside the API host
(`apps/api/src/winnie_api/devloop`): an ASGI middleware for unhandled exceptions
and 5xx answers, a root log handler for ERROR records from any capability, an
in-process detector for the services the API can probe, and a spool directory
that `scripts/watchdog.py` writes to while the API is down. Incidents dedupe by
fingerprint, a recovery row is stored resolved, and **Diagnose** makes one
read-only model call over a bounded evidence bundle. Design and phases are in
[self-repair.md](./self-repair.md).

**Why the host and not a capability package.** Collection observes every
capability's loggers and the whole ASGI app, and must keep working when a
capability is switched off. The same reasoning already keeps the usage ledger in
the host. A package would still need the host to install the middleware, and
`winnie-devloop` as a package would have to be imported by the host while
importing nothing back — a package that exists only to be called by one host is
the host, with extra steps. When P2 grows a worktree and its own state machine,
that part becomes a package because it owns state and tests of its own.

**Why the watchdog writes a spool instead of POSTing.** The incident that matters
most is "the API was down", and it cannot be posted to the API at the moment it
happens. A spool file also needs no credential on disk and no new unauthenticated
endpoint. The drain waits two seconds after the last write so a half-written line
is never read, and deletes a file only after every line in it was recorded.

**Why recovery is resolved on insert, and a resolved incident reopens.** A
recovery is history, not work: leaving it open puts an "index answered again"
row in the owner's list next to the outage it explains. A resolved incident that
recurs is open again because the failure is back; an ignored or acknowledged one
keeps the owner's decision, because that decision was about the failure and the
failure has not changed.

**Rejected: recording every 4xx.** Validation errors and 404s are the caller's
mistake, and they arrive in bursts that would bury a real 500. The switch exists
(`devloop.capture_client_errors`) but is off; a 5xx is always recorded.

**Rejected: deduplicating on the traceback or the exact message.** One bug
produces a new message per request (an id, a port, a count), which is how a log
viewer turns one failure into hundreds of rows. The fingerprint masks UUIDs, hex
identifiers and numbers of three digits or more, and adds the innermost
application frame — enough to separate two bugs and few enough to merge repeats.

**Rejected: letting the diagnostician read the repository with tools.** A tool
loop would be more capable and far less predictable: it costs many calls, it can
wander, and it makes the answer impossible to reproduce from the incident. The
bundle is assembled locally, capped at 24 KB, and every diagnosis is stored with
the model that produced it.

**Verified.** The live stack collected a stopped index as one `service_unhealthy`
incident with 12 occurrences and resolved it on the next successful probe; the
watchdog's spool entry for the same outage was drained within one detector tick
and auto-resolved by the recovery event; the console's 503s while the index was
down became one `http_error` incident with 4 occurrences. The Errors page was
driven in headless Firefox against a throwaway API: counts, filters, table,
detail panel, diagnosis and both themes render, and **Diagnose** without a bound
model answers `503 diagnosis is unavailable: no chat model is bound`.
