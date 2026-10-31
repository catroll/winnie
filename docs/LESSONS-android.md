# Android build lessons

What building and verifying `apps/android` in this environment taught us. Read
this before debugging the same failure again.

## The sandbox has no writable `$HOME` and no persistent `/tmp`

1. **Signal:** `touch $HOME/.dsh_write_test` returns
   `Read-only file system`, and a file written to `/tmp` in one bash call is
   gone in the next one (`FileNotFoundError` for a config created minutes
   earlier).
2. **Consequence:** the default Gradle and Android locations fail in confusing
   ways. Gradle cannot start (`Could not initialize native services. Failed to
   load native library 'libnative-platform.so'`), the SDK manager wants to write
   `$HOME/.android`, and Robolectric cannot create
   `$HOME/.robolectric-download-lock`.
3. **What works:** keep every toolchain root inside the checkout and export it:

   ```bash
   export HOME=$PWD/.home
   export GRADLE_USER_HOME=$PWD/.gradle-home
   export ANDROID_HOME=$PWD/.android-sdk
   export ANDROID_USER_HOME=$PWD/.android-user
   export JAVA_HOME=/usr/lib/jvm/java-25-openjdk-amd64
   ```

   Put test scratch under `.tmp/` (gitignored), not `/tmp`. The backend test
   config, SQLite file and uploads used by the Android integration test live in
   `.tmp/winnie-android/` for this reason.

## Robolectric needs a writable `user.home`

Even with the shell's `HOME` overridden, the test worker JVM receives
`user.home=/home/catroll` unless it is overridden explicitly. The first run
failed with:

```
java.lang.IllegalStateException: Couldn't create lock file /home/catroll/.robolectric-download-lock
```

`app/build.gradle.kts` sets `systemProperty("user.home", app/build/test-home)`
for every `Test` task and creates the directory in `doFirst`. The directory must
exist: pointing `user.home` at a missing path changes the message to
`Couldn't create lock file .../test-home/.robolectric-download-lock`, which is
the same failure with a different path.

## Current AndroidX requires API 37, and 37 is a minor version

With `compileSdk = 36`, `checkDebugAarMetadata` refused to build:

```
Dependency 'androidx.core:core-ktx:1.19.1' requires ... compile against version 37 or later
```

`compileSdk = 37` alone is not enough because the installed platform is
`android-37.2`; the AGP 9 DSL exposes `compileSdkMinor`:

```kotlin
compileSdk = 37
compileSdkMinor = 2
```

AGP 9 compiles Kotlin itself. Applying `org.jetbrains.kotlin.android` fails with
`Cannot add extension with name 'kotlin'`; pin the Kotlin version through a
root-level `buildscript` classpath entry instead, and apply only the Compose and
serialization plugins.

## No KVM means no emulator, so verification is layered

`/dev/kvm` is absent. The emulator cannot run here, and an APK that builds is
not a tested app. The verification used instead:

1. JVM unit tests for the HTTP client and WAV writer;
2. a Robolectric Compose test that renders the real login screen;
3. a JVM integration test (`BackendFlowIntegrationTest`) that uses the app's own
   `WinnieApi` against a running backend, gated on
   `WINNIE_ANDROID_BASE_URL`;
4. `assembleDebug`/`assembleRelease` plus `aapt2 dump badging` and
   `apksigner verify` on the APK.

The gap that remains is genuinely device-specific: microphone permission UX,
`AudioRecord` behaviour on real hardware, and UI rendering on a physical screen.
Do not describe the client as "device verified" until it has run on one.

## Stop `AudioRecord` before cancelling the pump

The recording loop blocks in `AudioRecord.read()` for one buffer. Cancelling
the coroutine first can leave it waiting, so `stop()` calls `record.stop()` to
unblock the read, then `cancelAndJoin()`, then releases the record. Calling
`read()` after `stop()` returns a negative error code, which the loop treats as
"stop" rather than an exception.

## Use the API's field names, not the ORM's

The Android DTOs mirror `meetings/schemas.py`: `detected_language`,
`translation_status`, `start_ms`, `local_language`, `audio_duration_ms`. The
first version of the upload test compared `request.path` to `"POST"` and failed
on a mistake in the test, not the client. Assert against the schema and check
the request method and path separately.

## An adaptive icon is drawn at 1.5x the bounds, so preview it at 72 dp

**Signal.** An icon that looked well proportioned in an SVG preview came out
over-tight in the launcher: the paper disc nearly touched the mask edge.

**Cause.** `AdaptiveIconDrawable` draws the 108 dp art into a rect 1.5x the
bounds, centred, and the launcher mask crops that to the middle 72 dp. A preview
that renders the 108 dp canvas as a square therefore shows 1.5x the margin the
device will. Measured on a 162 px bounds: the paper disc came out 136 px across,
i.e. 62/72 of the visible area, not 62/108.

**Check.** Render at the 72 dp the launcher shows, with both a circular and a
squircle mask, at 16/32/48/72/144 px. The squircle is the stricter case for a
disc composition, the circle for a full-bleed one. `make android` plus a
Robolectric rasterisation of the compiled drawable is the closest this machine
gets to a home screen (`LauncherIconTest`).

## A `VectorDrawable` clip lives in the group's space, and a hairline clip leaves a ring

**Signal.** The themed-icon layer was supposed to be the bird alone; it rendered
with a faint circle around it, and the painted area was scaled by an unexpected
factor.

**Cause 1.** `<clip-path>` inside a `<group>` is applied *after* the group's
matrix, so its path data is in the group's local coordinates, not the parent's.
An untransformed 108 dp circle there produces a clip of the wrong size and place.

**Cause 2.** The mark's frame is a compound path welded to the body, so the frame
cannot be deleted — it has to be clipped away. Clipping exactly on the frame's
inner radius leaves that hairline's antialiasing inside the clip: a few dozen
pixels at alpha 1-7 along a full circle. Panning it out by `1` canvas unit fixes
it; the only cost is the tail's last 1 dp, where the mark draws the body into the
ring anyway.

**What works.** Bake the placement into the path data with a strict parser (the
mark only uses absolute `M`/`L`/`A`/`Z`, so anything else should raise), then
keep clip paths in the same coordinate space as the path they clip. Verify the
baked form against the transform form by rasterising both and diffing: an exact
match (max channel difference 0) is achievable and much stronger than eyeballing.

## `Rect.isEmpty` is not an "uninitialised" flag

**Signal.** A bounding-box helper returned `Rect(100, 118 - 100, 118)` — a single
pixel — although the bitmap had thousands of opaque pixels.

**Cause.** The loop used `bounds.isEmpty` to detect the first hit, but `isEmpty`
is also true for a 1x1 rect (`left == right`), so every later pixel overwrote the
bounds instead of expanding them. A sentinel that is itself a valid state does
not mark "not set".

**Fix.** Track `right < 0` (or a boolean), and assert the result is non-empty
before using it — the assertion is what turned a wrong-looking icon into a
message that named the helper.

## A server error is not a network error: `ApiException : IOException` aborted the whole sync

**Signal.** Calendar, call log, SMS and contacts were switched on in the phone
data screen. The server showed the device registered and exactly **one** sync
state (`device`). Every run posted `POST .../sync` twice — `200`, then `500` —
and nothing after that kind was attempted, although the permission rows in the
app said all four scopes were ready.

**Cause 1, server.** The calendar batch carried
`Events.EVENT_TIMEZONE` as a Java `TimeZone.toString()` dump from the OEM
provider (Huawei/HarmonyOS observed):

```
java.util.SimpleTimeZone[id=Asia/Shanghai,offset=28800000,dstSavings=0,...]
```

267 characters against a 64-character column, so pydantic raised
`ValidationError` and the request became a 500.

**Cause 2, client.** `PhoneSyncEngine.step` rethrew `IOException` so that a
transport failure would stop the run — but `ApiException` *extends* `IOException`
(`data/WinnieApi.kt`), so the 500 was classified as a dead network and rethrown.
`device` had already uploaded; `calendar` failed; every scope after it (call,
message, contact, app, notification, location) was never attempted. One bad
field in one calendar row hid six kinds, and the UI said "Network unavailable.
The background sync will retry."

**Check.** Read the server access log, not the app: pair each
`POST /api/v1/phone/devices/{id}/sync` status with `phone_sync_states`. A run
that posts `200` then `500` and creates exactly one state row is this bug, not a
permission problem. The traceback names the model and field:
`ValidationError: 1 validation error for CalendarItem / timezone`.

**Fix.** Classify explicitly with `stopsRun(error)`: `UnauthorizedException` and
a real transport `IOException` stop the run, `ApiException` (any 4xx/5xx) stops
only its own scope and lands in `lastError`. Order matters — test
`ApiException` before `IOException`. On the server, a rejected item is a 422
naming the field, and the OEM timezone form is normalized to its Olson id
instead of being rejected.

**Generalisation.** A `catch` ordered by superclass silently reclassifies every
subclass. When one exception type means two different things — "I/O failed" and
"the server answered with an error" — split it with a predicate and unit-test
the predicate (`PhoneSyncPolicyTest`), not the handler.

## A failed provider read must not upload as an empty full snapshot

**Signal.** A phone with contacts and a synced calendar reported
`accepted_total = 0` for both `calendar` and `contact`, and the server showed a
*successful* sync.

**Cause.** `CalendarSource.readWindow` and `ContactsSource.readAll` wrapped the
primary provider query in `catch (_: Exception) { return emptyList() }`. Those
lists are uploaded with `mode: "full"`, where an empty list means "the phone has
nothing" — so a denied or broken provider read told the server to mark every
stored contact deleted (calendar deletions are windowed, contacts are not), and
recorded the run as a success, which made the permission failure invisible.

**Fix.** Let the read failure propagate so the engine records it against that
scope; keep the per-detail guards (numbers, emails, organisations) because a
missing email column costs an email, not the whole contact.

**Check.** A snapshot kind whose server count stays at `0` while the phone
clearly has data is a read failure, not an empty dataset — and a full snapshot
is an authoritative delete list, so "return empty on error" is a data-loss bug,
not a safe default.
