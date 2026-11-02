# Winnie Android client (MVP)

Kotlin/Compose client for Winnie. It has four tabs — **Chat**, **Meetings**,
**Phone** and **Account** — and signs in once against your own FastAPI backend.
The Chat tab talks to the assistant, Meetings records a meeting at 16 kHz mono,
streams live captions, uploads the WAV and shows the transcript and structured
minutes, Phone holds the opt-in phone-data sync and Account holds the
notification stream, the remembered password and sign-out. It contains no AI
logic: the FastAPI `/api/v1` contract is the source of truth.

## What works

- Sign in, or create the first account. The username and password are
  remembered on request, encrypted with an AES/GCM key held in the Android
  Keystore; the password field has a show/hide eye and the login screen has an
  explicit **Remember username and password** checkbox, on by default
- Bottom navigation: Chat, Meetings, Phone, Account. Detail screens (meeting,
  recorder, conversation history, alerts) open on top of the tabs
- Assistant chat with Markdown rendering — headings, lists, tables, fenced code,
  quotes and links — a conversation history that includes every channel, and
  tool output collapsed behind **Show**
- Record 16 kHz mono PCM to a WAV file and upload it
- Live captions over a WebSocket while recording; the final transcript still
  comes from the uploaded file
- Meeting list with status, transcript with original + translation, minutes
- Server address editable on the login screen and persisted
- Phone data sync: calendar, call log, SMS, contacts, notifications, one
  location fix per sync, installed apps and a device profile, each an explicit
  opt-in; confirmed calendar and SMS writes from the assistant
- Reminders from the server: a foreground service holds the push WebSocket,
  posts alerts, and wakes the phone to run a confirmed command immediately
- Wake word: say "winnie" and the chat opens a hands-free turn. The keyword is
  detected on the phone by an offline model; only the utterance after the wake
  is uploaded for transcription.
- Voice output: a dictated turn's reply is spoken as soon as it arrives, and
  every reply has a speaker button to read it again or stop it. Account → Voice
  output holds the on/off switch, four rates and a preview. The speaking is done
  by the phone's own text-to-speech engine — no vendor and no network — and its
  engine and voice are chosen in the system's accessibility settings. See [docs/voice-wake.md](../../docs/voice-wake.md)

Not implemented yet: streaming assistant replies (one response per turn),
attachments in chat, foreground-service recording (a backgrounded
app can be killed, and that also stops the live-caption stream), MMS and
attachments, SMS delivery receipts, a command poll separate from the data sync,
offline cache, export/share, speaker labels, certificate pinning, and automatic
recovery from an expired session. The meeting language is fixed to `zh` (the
server's local language) in this MVP.

## Sign-in and remembered credentials

The login form pre-fills the username and password saved by the last successful
sign-in, so returning to the app is one tap. Both values are sealed with the
Android Keystore key before they reach `SharedPreferences` — a copied data
directory does not reveal them. The trade-off is deliberate and visible:

- Anyone who can unlock the phone and open Winnie can reveal the saved password
  with the eye icon. That is the point of remembering it, and it is why the
  checkbox is explicit rather than automatic.
- Unticking the checkbox and signing in deletes the pair. So does **Forget** on
  the Account tab.
- A failed sign-in never replaces the saved pair, so a typo does not lose the
  credentials that used to work.
- Signing out keeps the saved pair (that is what "remember" means); it deletes
  the session token and the id of the open conversation, so a different account
  on the same phone cannot resume someone else's transcript.
- This is not Android autofill or Credential Manager. Those need an app
  association and a credential provider account for a publicly reachable
  domain; a self-hosted instance on a LAN has neither. The local encrypted pair
  is the honest alternative, not a replacement.

## Assistant chat

**Chat** starts a conversation, resumes the one that was open when the app was
last used, and opens **history** from the clock icon. Every conversation is
listed regardless of where it started — the console, the phone web page, an API
client or a scheduled task — because the server keeps one history. A scheduled
task run is read-only and is marked as such.

A reply is rendered as Markdown by
[`multiplatform-markdown-renderer`](https://github.com/mikepenz/multiplatform-markdown-renderer):
a heading, a bullet list, a table, a fenced code block and a link all render as
Compose UI rather than plain text, and links open in the browser. A table wider
than a phone bubble scrolls horizontally. Heading sizes are mapped down to the
app's title styles; the library's defaults are article-scale and would render
`##` at 45 sp inside a bubble.

Tool calls appear as a collapsed **Tool · name** row with the raw output behind
**Show**, so the conversation reads like a conversation. A failed turn is
removed from the list and its text returns to the composer: the server never
stored it, and a bubble that disappears on reload would lie about the
transcript.

The client uses the `api` channel for every turn, which keeps phone messages
distinguishable from console and web ones in the console history.


## Phone data

Open the **Phone** tab. Nothing is shared until the owner turns on a scope; each
switch requests that scope's permission first.

| Scope | Android permissions | Special access |
|---|---|---|
| Calendar | `READ_CALENDAR`, `WRITE_CALENDAR` | — |
| Call log | `READ_CALL_LOG` | — |
| SMS | `READ_SMS`, `SEND_SMS` | — |
| Contacts | `READ_CONTACTS` | — |
| Notifications | — | Notification access (system settings) |
| Location | fine/coarse location | "Allow all the time" for background fixes |
| Installed apps | `QUERY_ALL_PACKAGES` | — |
| Device info | `GET_ACCOUNTS` for the account list | — |

`READ_SMS`, `SEND_SMS` and `READ_CALL_LOG` are allowed by Android once granted,
but **Google Play restricts them** for apps that are not the default SMS
handler or a declared core-function exception. This client is for personal
sideloading; that is a distribution decision, not a defect.

How syncing behaves:

- **While the app process is alive the sync loop ticks every 5 seconds** — the
  app being open, or the alert foreground service keeping the process alive.
  A tick is a *local change check*, not an upload: each scope has a cheap token
  (a cursor probe for SMS/calls, a digest for a snapshot, a time bucket for
  location/device), and only a scope whose token differs from the last accepted
  upload is sent. Cursor kinds then send only the rows above their cursor, so a
  new SMS uploads one message rather than re-reading the phone.
- Cheap scopes are checked on every tick; calendar every 30 s, contacts every
  60 s, location and device every 5 minutes, installed apps every 30 minutes.
  A content observer on SMS, call log, calendar or contacts marks its scope
  dirty and wakes the loop immediately, so a change it sees does not wait for
  the slower cadence.
- The screen shows this policy next to the master switch, including the
  background interval: when Android kills the process there is no 5-second
  loop, and WorkManager's periodic sync (30 minutes by default, 15-minute
  floor) is the fallback. The server's `interval_minutes` hint updates it after
  each full sync.
- A full sync (the app's button, a scope toggle, the periodic worker) reads
  every enabled scope. The 5-second path reads only the scopes the check found
  changed, plus a pending-command poll at most every 30 s — and immediately
  when the push stream sends a `sync` frame.
- A provider that fails (a revoked permission, a vendor calendar that rejects a
  column) records an error and the other scopes still upload; a repeated
  per-kind failure backs the loop off instead of retrying it every 5 seconds.
- Notifications are captured by a `NotificationListenerService` into a bounded
  private file (2,000 entries) and uploaded on the next sync. Ongoing
  notifications and the app's own notifications are skipped.
- Location records one fix every five minutes while the loop runs, reusing a
  last-known fix younger than five minutes. It is deliberately not sampled on
  every 5-second tick: a GPS fix that often would cost more battery than the
  data is worth. Background fixes need "allow all the time"; without it the fix
  is skipped and the rest of the sync continues.
- The assistant's confirmed writes arrive as commands on the same sync:
  calendar create/update/delete and SMS send. A calendar write needs a writable
  calendar; an SMS send is submitted to the carrier (delivery is not
  confirmed).
- **Delete data on server** removes every synced record and resets the local
  cursors, so a later sync uploads the selected scopes again. Turning a scope
  off only stops future uploads.

Known limits: the 5-second cadence runs only while the process is alive;
SMS/MMS attachments are not read; SMS edits and deletions made in
another app are not propagated; recurring calendar events sync as one event with
its recurrence rule, not one row per occurrence; a sync interrupted by process
death resumes from the last stored cursor.

The server-side contract, retention windows and privacy position are in
[docs/phone.md](../../docs/phone.md).

## Push notifications

Open **Account → Alerts & background**. The switch starts a foreground service
that keeps one WebSocket to `/api/v1/push/ws` open, so an assistant reminder, a
scheduled task's brief or a finished phone action arrives without the phone
polling.

- The service uses the `specialUse` foreground-service type: the standard types
  do not describe a self-hosted notification socket, and `dataSync` is capped at
  six hours a day on Android 15. A persistent low-importance notification is
  shown while it runs, with a **Stop** action.
- Reconnect uses exponential backoff (2 s to 60 s). `START_STICKY` restarts a
  killed service, and a boot receiver restarts it after a reboot or an app update
  when the owner had it enabled.
- A frame with `kind: "sync"` is a control message: it runs the phone-data sync
  immediately, which is how a confirmed calendar change or SMS takes effect in
  seconds rather than at the next 30-minute sync.
- Two notification channels: `winnie_background` for the service, `winnie_alerts`
  for reminders, so one can be silenced without the other.
- **Send test notification** publishes an event on the server. It proves the
  whole path; if it does not arrive, the screen shows the connection state, the
  last error and whether notifications are blocked.
- Android 13+ needs `POST_NOTIFICATIONS`; the switch requests it. Battery
  optimization is offered as an exemption because some vendors kill background
  services regardless. Both are shown as explicit states on the screen.
- Signing out stops the service. There is no delivery while the process is
  force-stopped; the backlog replays on the next connect.

There is no FCM or other vendor push service, which is why this needs a
foreground service. The protocol, cursor, privacy and limits are in
[docs/push.md](../../docs/push.md).

## Requirements

- JDK 17+ (verified with JDK 25)
- Android SDK platform 37.2 and build-tools 37.0.0
- Gradle wrapper 9.8.2 (downloads itself)

The current AndroidX libraries require API 37, and `compileSdk = 37` alone
resolves to the 37.0 platform, so the module sets `compileSdkMinor = 2`.

## Build

From the repository root, `make android` runs the build with the JDK, SDK and
Gradle caches kept inside the checkout (see
[`docs/LESSONS-android.md`](../../docs/LESSONS-android.md)):

```bash
make android-kws   # once: the 47 MB sherpa-onnx AAR for the wake word
make android
# apps/android/app/build/outputs/apk/debug/app-debug.apk
```

`make android` stops with a clear message when the AAR is missing. The AAR is a
GitHub release asset rather than a Maven Central dependency, so it is fetched
once into `apps/android/app/libs` and gitignored; the keyword model under
`app/src/main/assets/kws` is committed, so nothing else is downloaded. The
build keeps two ABIs (arm64-v8a, x86_64) and drops the C and C++ API shared
libraries the app does not call, which is what keeps the debug APK near 73 MB
instead of 139 MB.

The default server address is baked into the APK from `WINNIE_PUBLIC_URL`.
`make android` sources it from `apps/api/.env`; when unset, the build falls back
to `http://10.0.2.2:8000`.

Or invoke Gradle directly (export `WINNIE_PUBLIC_URL` yourself in this case):

```bash
cd apps/android
echo "sdk.dir=/path/to/android-sdk" > local.properties   # or export ANDROID_HOME
./gradlew :app:assembleDebug
# app/build/outputs/apk/debug/app-debug.apk
```

The debug APK is signed with the debug key; install it with
`adb install -r app/build/outputs/apk/debug/app-debug.apk`. The release build is
unsigned, and shrinking/obfuscation is not enabled yet.

## Run against the backend

Start the API with accounts and meetings enabled. For a credential-free demo,
use the fake providers:

```yaml
auth:
  enabled: true
  allow_registration: true
meetings:
  asr_provider: fake
  translator: fake
  summarizer: fake
```

With the fallback build, `http://10.0.2.2:8000` reaches the host from the
emulator. For a phone, set `WINNIE_PUBLIC_URL` to the server's reachable address
— for example `http://eqr.hosts.d3v.cn:8000` — and rebuild the APK:

```bash
WINNIE_PUBLIC_URL=http://eqr.hosts.d3v.cn:8000 make android
```

`make api` binds `[::]` for IPv6; use `API_HOST=0.0.0.0` only on an IPv4-only
network. Allow port 8000 through the firewall. The MVP permits cleartext HTTP
for the local network. A public IPv6 domain is globally routable, so HTTP there
sends passwords, tokens and meeting audio in clear: terminate TLS before using
it with real accounts.

With `meetings.asr_provider: volcengine`, the API process needs
`VOLCENGINE_ASR_API_KEY`; the app itself never sees vendor credentials. Live
captions use the same API key through the server-side streaming adapter and
`wss://…/api/v1/meetings/{id}/stream`. The final transcript is a second,
file-based ASR call on the uploaded WAV, which is the accuracy tradeoff for
keeping one canonical pipeline.

## Languages

The UI ships in **English (default) and Simplified Chinese**. Android picks the
language from the system locale, so a phone set to Chinese shows the Chinese app
with no setting to find. On Android 13+ `android:localeConfig` also adds Winnie
to the system's per-app language page (Settings → Apps → Winnie → Language) on
older versions the system locale is the only control.

- Every user-facing string lives in `res/values/strings.xml`; the Chinese
  translation is `res/values-zh/strings.xml`, and plurals are in both.
- `StringResourceParityTest` compares the two files key by key and format
  argument by format argument, because Android silently falls back to English
  for a missing key and would otherwise ship a half-translated screen.
- `ChineseStringsTest` runs under a `zh` qualifier and asserts the rendered
  values, and `LayoutScreenshotTest` renders several screens in Chinese so
  longer translations are looked at, not assumed to fit.
- `AppStrings` gives the non-Composable code (view models, sync engine,
  notifications) the same resources; the application context resolves them
  against the same locale as `stringResource`.
- Deliberately **not** translated: data that leaves the app — assistant replies,
  server error details, calendar/SMS command results reported back to the
  server, and provider exception text. Those are records, not UI chrome; the
  assistant answers in whatever language the conversation uses.

## Tests

```bash
./gradlew :app:testDebugUnitTest
```

- `WinnieApiTest` — request/response handling against an in-process HTTP server,
  including the assistant chat and conversation endpoints
- `WavFileWriterTest` — WAV header sizes and patching
- `LoginScreenTest` — Robolectric render of the login screen: prefill, the
  password eye, the remember checkbox, registration mode
- `CredentialStoreTest` — the remembered pair is sealed, cleared and never
  written in clear text; an unreadable payload is dropped
- `SessionManagerTest` — a successful sign-in remembers, an opt-out clears, and
  a failed attempt keeps the pair that used to work
- `ChatScreenTest` — Robolectric render of the assistant tab: Markdown, the
  thinking row, the composer, collapsed tool output, error dismissal
- `AccountScreenTest` — Robolectric render of the account tab, the saved-password
  state, the forget and sign-out confirmations
- `LayoutScreenshotTest` — renders Chat, history, Account, Phone, alerts and
  login at 360×800 dp, in English and Chinese, and writes PNGs under
  `build/screenshots/`; the only way to see a layout on a machine with no
  emulator (see `docs/LESSONS-android.md`)
- `PhoneApiTest` — phone endpoint paths, bodies and responses against an
  in-process HTTP server
- `PhoneSourceMappingTest` — SMS, call-log and calendar cursor mapping
- `PhonePrefsTest` — scope opt-in, cursors, change fingerprints and check times
- `PhoneChangeDetectorTest` — which scopes a 5-second tick reads and uploads:
  cadence, observer bypass, unchanged tokens, unreadable providers
- `PhoneSyncPolicyTest` — transport/session/revoked-device errors end a run,
  one rejected kind does not
- `StringResourceParityTest` — every English key and format argument exists in
  Chinese, and vice versa
- `ChineseStringsTest` — the Chinese resources resolve under a `zh` qualifier
- `NotificationBufferTest` — the bounded notification queue's append/ack/trim
- `PhoneDataScreenTest` — Robolectric render of the consent screen
- `PushModelsTest` — the WebSocket frame contract, including unknown fields
- `PushRoutingTest` — a reminder becomes a notification, `sync` becomes a sync
- `PushPrefsTest` — the delivery cursor and connection state
- `PushApiTest` — the REST backlog, status and test endpoints
- `PushSettingsScreenTest` — Robolectric render of the alerts screen
- `BackendFlowIntegrationTest` — skipped unless `WINNIE_ANDROID_BASE_URL` is set;
  runs register → create → upload → transcribe → transcript → minutes → delete
  against a real backend using the app's own HTTP client

```bash
WINNIE_ANDROID_BASE_URL=http://[::1]:8000 \
  ./gradlew :app:testDebugUnitTest --rerun
```

## Layout

```text
app/src/main/java/dev/winnie/app/
├── data/      HTTP client, DTOs, session/token/credential storage, settings
├── audio/     AudioRecord wrapper and incremental WAV writer
├── phone/     scopes, permissions, sources, change detection, sync loop/engine
├── push/      the foreground notification stream and its notification/routing
├── AppStrings.kt  resources for view models, sync code and notifications
└── ui/
    ├── AppRoot.kt          login gate and session state
    ├── AppShell.kt         bottom-bar shell, tabs and overlay routes
    ├── Components.kt       shared small composables
    ├── account/            identity, saved password, sign-out
    ├── chat/               assistant chat and conversation history
    ├── home/               meetings tab
    ├── login/              sign-in and remembered credentials
    ├── meeting/            transcript and minutes
    ├── phone/              phone-data consent and status
    ├── push/               alerts and background connection
    ├── record/             recorder and live captions
    └── theme/              Material 3 theme
```

The signed-in shell owns the bottom bar and the four tabs; a detail screen is a
full-screen route with no bar, chosen by `rememberSaveable` state rather than a
navigation library. The back stack is four overlays deep at most, so a graph
would be more machinery than the screen set needs.

Recording stops when the activity is destroyed; the follow-up is a foreground
service. Transcription uses the file-first API, so the backend's job backend
determines whether `POST /transcribe` returns immediately or after processing;
the client waits and then polls the meeting status either way.
