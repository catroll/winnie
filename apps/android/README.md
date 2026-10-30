# Winnie Android client (MVP)

Kotlin/Compose client for the Winnie meeting-notes backend. It signs in, records
a meeting at 16 kHz mono, uploads it, waits for the pipeline, and shows the
transcript and structured minutes. It contains no AI logic: the FastAPI
`/api/v1` contract is the source of truth.

## What works

- Sign in, or create the first account; the bearer token is encrypted with an
  AES/GCM key held in the Android Keystore
- Record 16 kHz mono PCM to a WAV file and upload it
- Meeting list with status, transcript with original + translation, minutes
- Server address editable on the login screen and persisted

Not implemented yet: foreground-service recording (a backgrounded app can be
killed), offline cache, export/share, realtime streaming, speaker labels,
certificate pinning, and automatic recovery from an expired session. The
meeting language is fixed to `zh` (the server's local language) in this MVP.

## Requirements

- JDK 17+ (verified with JDK 25)
- Android SDK platform 37.2 and build-tools 37.0.0
- Gradle wrapper 9.8.2 (downloads itself)

The current AndroidX libraries require API 37, and `compileSdk = 37` alone
resolves to the 37.0 platform, so the module sets `compileSdkMinor = 2`.

## Build

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

In the emulator the default server address `http://10.0.2.2:8000` reaches the
host. On a phone, enter the computer's LAN address on the login screen, bind
uvicorn to `0.0.0.0`, and allow port 8000 through the firewall. The MVP permits
cleartext HTTP for the local network; put the server behind TLS before exposing
it more widely.

With `meetings.asr_provider: volcengine`, the API process needs
`VOLCENGINE_ASR_API_KEY`; the app itself never sees vendor credentials.

## Tests

```bash
./gradlew :app:testDebugUnitTest
```

- `WinnieApiTest` — request/response handling against an in-process HTTP server
- `WavFileWriterTest` — WAV header sizes and patching
- `LoginScreenTest` — Compose UI smoke test under Robolectric, no emulator needed
- `BackendFlowIntegrationTest` — skipped unless `WINNIE_ANDROID_BASE_URL` is set;
  runs register → create → upload → transcribe → transcript → minutes → delete
  against a real backend using the app's own HTTP client

```bash
WINNIE_ANDROID_BASE_URL=http://127.0.0.1:8000 \
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
