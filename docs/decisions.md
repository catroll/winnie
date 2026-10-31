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
