# Winnie Android client (MVP)

Kotlin/Compose client for the Winnie meeting-notes backend. It signs in, records
a meeting at 16 kHz mono, streams live captions while recording, then uploads
the WAV, waits for the pipeline and shows the final transcript and structured
minutes. It contains no AI logic: the FastAPI
`/api/v1` contract is the source of truth.

## What works

- Sign in, or create the first account; the bearer token is encrypted with an
  AES/GCM key held in the Android Keystore
- Record 16 kHz mono PCM to a WAV file and upload it
- Live captions over a WebSocket while recording; the final transcript still
  comes from the uploaded file
- Meeting list with status, transcript with original + translation, minutes
- Server address editable on the login screen and persisted
- Phone data sync: calendar, call log, SMS, contacts, notifications, one
  location fix per sync, installed apps and a device profile, each an explicit
  opt-in; confirmed calendar and SMS writes from the assistant

Not implemented yet: foreground-service recording (a backgrounded app can be
killed, and that also stops the live-caption stream), MMS and attachments,
SMS delivery receipts, a command poll separate from the data sync, offline cache,
export/share, speaker labels, certificate pinning, and automatic recovery from
an expired session. The
meeting language is fixed to `zh` (the server's local language) in this MVP.

## Phone data

Open **Phone data** from the home screen. Nothing is shared until the owner
turns on a scope; each switch requests that scope's permission first.

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

- WorkManager runs a periodic sync every 30 minutes by default (Android's floor
  is 15). The server's `interval_minutes` hint updates it after each sync.
- Content observers on SMS, call log, calendar and contacts request a debounced
  one-shot sync while the app process is alive; new notifications do the same.
- A sync uploads the enabled scopes one kind at a time, in pages of 200. A
  provider that fails (a revoked permission, a vendor calendar that rejects a
  column) records an error and the other scopes still upload.
- Notifications are captured by a `NotificationListenerService` into a bounded
  private file (2,000 entries) and uploaded on the next sync. Ongoing
  notifications and the app's own notifications are skipped.
- Location records one fix per sync, reusing a last-known fix younger than five
  minutes. Background fixes need "allow all the time"; without it the fix is
  skipped and the rest of the sync continues.
- The assistant's confirmed writes arrive as commands on the same sync:
  calendar create/update/delete and SMS send. A calendar write needs a writable
  calendar; an SMS send is submitted to the carrier (delivery is not
  confirmed).
- **Delete data on server** removes every synced record and resets the local
  cursors, so a later sync uploads the selected scopes again. Turning a scope
  off only stops future uploads.

Known limits: SMS/MMS attachments are not read; SMS edits and deletions made in
another app are not propagated; recurring calendar events sync as one event with
its recurrence rule, not one row per occurrence; a sync interrupted by process
death resumes from the last stored cursor.

The server-side contract, retention windows and privacy position are in
[docs/phone.md](../../docs/phone.md).

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
make android
# apps/android/app/build/outputs/apk/debug/app-debug.apk
```

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

## Tests

```bash
./gradlew :app:testDebugUnitTest
```

- `WinnieApiTest` — request/response handling against an in-process HTTP server
- `WavFileWriterTest` — WAV header sizes and patching
- `LoginScreenTest` — Compose UI smoke test under Robolectric, no emulator needed
- `PhoneApiTest` — phone endpoint paths, bodies and responses against an
  in-process HTTP server
- `PhoneSourceMappingTest` — SMS, call-log and calendar cursor mapping
- `PhonePrefsTest` — scope opt-in, cursors and the interval floor
- `NotificationBufferTest` — the bounded notification queue's append/ack/trim
- `PhoneDataScreenTest` — Robolectric render of the consent screen
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
├── data/      HTTP client, DTOs, session/token storage, server settings
├── audio/     AudioRecord wrapper and incremental WAV writer
├── ui/        Compose screens: login, meeting list, recorder, meeting detail
└── AppContainer.kt
```

Recording stops when the activity is destroyed; the follow-up is a foreground
service. Transcription uses the file-first API, so the backend's job backend
determines whether `POST /transcribe` returns immediately or after processing;
the client waits and then polls the meeting status either way.
