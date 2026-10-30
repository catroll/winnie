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
