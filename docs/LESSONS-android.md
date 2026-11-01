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

## "The server took too long to respond" is a TCP deadline, not a busy server

**Signal.** One handset (NAM-AL00) failed every "Sync now" with *The server took
too long to respond*, while the other handset on the same account synced
normally and the server logged no request at all for the failing device.

**Cause.** That sentence is `friendlyMessage()` for `SocketTimeoutException`
(`data/Errors.kt`) — the 15 s connect or 60 s read deadline of
`defaultHttpClient`, not a server-side stall. The APK's baked-in address was
`http://eqr.hosts.d3v.cn:8000`; its **A record was `192.168.31.31`**, a LAN
address from a previous network, while the host is `192.168.1.38`. Android took
the IPv4 path, the SYN went to the default gateway and was silently dropped, and
the connect deadline produced the message. The AAAA record pointed at the real
host, which is why the same phone had synced for hours over IPv6 (its access-log
entries come from the home `/64`) and stopped the moment it picked IPv4.

**Check, in this order.**

```bash
# 1. What does the client dial, and what does each family say?
dig +short A    eqr.hosts.d3v.cn     # 192.168.31.31  <- not this host
dig +short AAAA eqr.hosts.d3v.cn     # the host's global address
ip -4 addr show scope global | grep inet

# 2. Probe the address the client would pick, with a deadline. A silent hang
#    reproduces the phone's exception; a refusal would be a different message.
curl -s -m 8 -o /dev/null -w 'code=%{http_code}\n' http://192.168.31.31:8000/healthz
# code=000, exit=28  (timeout, no RST, no ICMP)

# 3. Read the access log by device id, not by eye. uvicorn logs a line only
#    when a response completes, so zero lines means nothing arrived.
grep -c "5c146cec" .cache/logs/api.log
```

An external IPv4 checker separates the two failures cleanly:
`check-host.net/check-tcp?host=<name>:8000` returned *Connection timed out* for
the name (dead private A) but *Connection refused* for the public address
`117.152.145.159:8000` (router does not forward the port). "Refused" would have
shown *Could not connect to the server*; only the silent drop shows the timeout
sentence.

**Fix.** Publish an A record that is the host (`192.168.1.38`) or set the
address explicitly on the phone; a LAN URL removes DNS from the path entirely.
Off the LAN this deployment has no usable IPv4 path at all (the public address
RSTs 8000), so remote sync depends on inbound IPv6 reaching the host.

**Generalisation.** When a client reports a timeout, resolve the name it dials
for **both** families and probe the address it actually chooses. A wrong DNS
record, a stale DHCP lease or a port-forward that was never added all surface as
"the server is slow" if the only evidence is the sentence the app shows. The
tell is asymmetry: one device and one address family fine, another silent, with
no request in the server log.

## A decorative `weight` next to a `weight` halves the row

**Signal.** A settings row on a 360 dp screen wrapped its title onto two lines
while leaving a wide empty gap before the trailing chevron. The subtitle wrapped
earlier than the row's width allowed, and the row looked like it was reserving
space for something that was not there.

**Cause.** The row had both `Column(Modifier.weight(1f))` for the text and
`Spacer(Modifier.weight(1f))` before the icon. Every weighted child shares the
free space, so the text got half of it. A spacer that exists only to push a
trailing element right is never needed when another child already has `weight`.

**Check.** In a `Row`, list the weighted children and add up their weights. If a
`Spacer` carries `weight`, ask what it is competing with. `git grep
'weight(1f)' <file>` on a suspicious layout is enough; the screenshot shows the
symptom, the modifier list shows the cause.

## Robolectric can rasterise the screen, so look at it instead of reasoning

**Signal.** A Markdown reply rendered `##` at 45 sp inside a chat bubble, and a
title wrapped at a width that arithmetic said it could not. No assertion was
failing; the layout was simply wrong.

**What works.** With `@GraphicsMode(GraphicsMode.Mode.NATIVE)` and a phone
qualifier, a Compose test can capture real pixels:

```kotlin
@Config(qualifiers = "w360dp-h800dp-xhdpi")
class LayoutScreenshotTest {
    @get:Rule val compose = createComposeRule()

    @Test fun chat() {
        compose.setContent { WinnieTheme { ChatScreen(...) } }
        compose.waitForIdle()
        val bitmap = compose.onRoot().captureToImage().asAndroidBitmap()
        File("build/screenshots/chat.png").outputStream().use {
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
        }
    }
}
```

`build/screenshots/*.png` is gitignored with the rest of `build/`. This is the
only visual check available without `/dev/kvm`, and it caught both problems
above in one run. Keep it as a test that writes images and asserts nothing about
pixels; a human (or an image-reading agent) looks at the result.

**Corollary.** A Compose test's window is not the device's. After the login form
grew by two rows, `onNodeWithText("Sign in").assertIsDisplayed()` failed because
the button was below the fold; `performScrollTo()` is the fix. When a screen
grows, expect the older assertions to need scrolling, not that the screen broke.

## `kotlinx.serialization` does not send a property that has a default

**Signal.** The chat request worked against the real server, but the wire test
failed on `assertTrue(body.contains("\"channel\":\"api\""))`: the field was
absent from the JSON. The server has the same default, so only the assertion
noticed.

**Cause.** `encodeDefaults` is `false` by default, and it applies to *every*
defaulted property whether or not the value differs from the default.

**Fix.** Either drop the default from the DTO and pass the value explicitly, or
annotate the property `@EncodeDefault`. Test the field that must be on the wire;
"the server accepted it" does not prove the client sent it.

## The Android Keystore does not exist under Robolectric

**Signal.** A store that calls `KeyStore.getInstance("AndroidKeyStore")` cannot
be unit-tested: Robolectric ships no provider or shadow for it, so the first
`Cipher.init` fails and the test proves nothing about the persistence rules.

**Fix.** Put the encryption behind `SecretBox` (`seal`/`open`) and inject a fake
in tests. `TokenStore` and `CredentialStore` then test what actually needs
testing — that values are sealed before they reach `SharedPreferences`, that a
failed sign-in does not overwrite the saved pair, that an unreadable payload is
dropped rather than returned. The AES implementation itself is the Android
platform's, not this app's.

## "Every 5 seconds" cannot be a WorkManager period, and must not be an upload

**Signal.** The requirement was a 5-second sync. The obvious change — pass 5 to
the periodic worker — is impossible: `PeriodicWorkRequestBuilder` documents a
15-minute minimum, and WorkManager silently clamps anything shorter. The other
obvious change — run the whole sync every 5 seconds — uploads device, calendar,
contacts, apps and location on every tick: ~157 KB and eight requests, 720 times
an hour.

**What works instead.** Tick every 5 seconds in the process, but make a tick a
*local check*:

1. Each scope keeps a persisted cheap token after a successful upload: a cursor
   probe for append-only kinds, a digest for a snapshot, a time bucket for
   sampled kinds.
2. Only the scopes whose token differs are uploaded, and cursor kinds send only
   the rows above their cursor.
3. Expensive scopes check on their own cadence (30 s to 30 min); a content
   observer marks a scope dirty and wakes the loop, so the slow cadence does not
   delay a change the provider already announced.
4. The loop lives in `Application.onCreate`, so it runs while the app is open
   and while the alert foreground service keeps the process alive. When Android
   kills the process, the WorkManager worker is the fallback — and the UI says
   so instead of implying a guarantee the platform does not give.

**Trap: reconfiguring WorkManager on every tick.** The old engine called
`PhoneSyncScheduler.configure()` after every run. At 5 seconds that is a
WorkManager database write (`enqueueUniquePeriodicWork(UPDATE)`) twelve times a
minute. Reconfigure only after a full run, where the server's interval hint is
actually read.

**Trap: a stale API client after sign-out.** The loop caches one `WinnieApi` so
it does not decrypt the Keystore token twelve times a minute. Checking the cache
before the session state kept a revoked client alive after logout and could
upload with the old token. Check the state first: `LoggedOut` clears the cache
and returns null; `Restoring` may use the disk token once (a headless
push-service process has no UI to restore).

## Resource qualifiers are ordered, and Robolectric enforces it

**Signal.** `@Config(qualifiers = "w360dp-h800dp-xhdpi-zh")` fails with:

```
Failed to parse qualifier string '...': Found unrecognized trailing qualifier segments zh
```

**Cause.** Android's resource qualifier order puts the locale first, before
smallest width, width, height and density. The valid string is
`zh-w360dp-h800dp-xhdpi`, not the other way round. A method-level `@Config`
replaces the class-level one, which is how one test class renders both locales.

**And.** `values-zh` covers Chinese regardless of region; the system locale picks
it with no code. But Android **silently falls back** to the default resource for
a missing key, so a half-translated build looks fine in code review. Compare the
two XML files in a unit test — key set and `%1$s`-style arguments — because the
resource merger will not report a hole.

## A global string accessor needs one owner and one initialiser

**Signal.** View models, the sync engine and notification builders have no
Composable context, so `stringResource` is unavailable, and threading a Context
through every constructor just to build one sentence is noise.

**What works.** `AppStrings` holds the application context, is initialised once
in `WinnieApplication.onCreate`, and resolves against the same system locale as
`stringResource`. Robolectric instantiates the real Application, so tests get the
same initialisation without extra setup; a pure JVM test that calls
`friendlyMessage()` would need it, which is why those tests avoid it.

**Trap: `stringResource` inside a non-composable lambda.**
`onClick = { onSuggestion(stringResource(R.string.x)) }` does not compile
("Composable invocations can only happen from the context of a @Composable
function"). Resolve the string before the lambda and capture the value.

## "Granted" and zero rows is a privacy answer, not an empty phone

**Signal.** One handset (HUAWEI TAS-AL00) recorded a *successful* `calendar` and
`contact` sync with `accepted_total = 0`, never sent `call` or `message` at all,
yet its notification rows named real SMS senders (`【阿里云】…`, `【滴滴数科】…`)
and a real calendar event (`日历 · 吴倩的生日 · 今天 全天`). The app's own
phone-data screen said every scope was enabled and showed no error, and the
system permission screen said 电话/信息/通讯录/日历 = 允许.

**Two explanations fit that surface, and they need different fixes:** the phone
really has no rows, or the phone is not allowed to see its rows. Counting rows
on the server cannot tell them apart — both are zero.

**What settled it.** Ship the answer in the data the phone already uploads. Each
device snapshot now carries `scope_read`, five cheap count probes:

```json
{"uid":10329,"data_dir":"/data/user/0/dev.winnie.app/files","package":"dev.winnie.app",
 "message":{"permission":"granted","rows":0},"call":{"permission":"granted","rows":0},
 "contact":{"permission":"granted","rows":0},"calendar":{"permission":"granted","rows":0},
 "calendars":{"permission":"granted","rows":0}}
```

Granted, no exception, no null cursor: the provider answered a cursor with no
rows. The same APK on NAM-AL00 read 2000 calls, 1462 SMS, 81 contacts and 47
events, so the code was never the variable. The ROM's "blank data" privacy gate
(空白通行证 / 虚拟权限, and the app guard that covers sideloaded APKs) keeps the
grant and blanks exactly the four data classes — calls, SMS, contacts, calendar —
which is precisely the set that failed.

**Why it was invisible for hours.** Three layers agreed with each other:
`checkSelfPermission` said granted, `ContentResolver.query` returned a cursor, and
the sync looked successful with zero accepted rows. Nothing in the chain had a
reason to complain. The generic lesson is about *where* to look: when a whole
class of reads returns nothing while an unrelated class works, probe the
provider's raw answer and compare it with the permission state before touching
the query code — a silent "no" from the platform is not a bug in the caller.

**What the code does now.**
- `scope_read` travels with every device snapshot and is shown on the console's
  device card, so an empty tab is explained rather than implied.
- A full snapshot that reads zero rows although the previous upload carried some
  is **not sent**: it would be an authoritative delete list for the server copy.
  The scope records an error naming the previous count; wiping the server copy
  first clears the counter and is the deliberate way to say "they really are
  gone".
- A blank answer is a per-app ROM setting, not something the server can grant:
  the remedy is the owner's (turn the blank-data switch off for this app), and
  the console now says so instead of showing "0 联系人".

**It blanks the write path too.** The same guard made `calendar.create` fail
with `No writable calendar is available on this phone`: `CalendarSource.writableCalendar`
queries `CalendarContract.Calendars` with `CALENDAR_ACCESS_LEVEL >= CONTRIBUTOR`,
and a blanked provider answers with zero rows, so the phone concludes it has no
calendar at all. The message names the wrong thing — there *was* a writable
calendar, the app just could not see it. After the guard was removed the same
command succeeded (`Created event 5320 in 278895860@qq.com`) and the event came
back through the next sync. So: a write that fails for "nothing to write to"
on a phone whose reads are also thin is one diagnosis, not two, and the fix is
the system setting rather than the query.

**The send is gated by a dialog the app cannot see.** With `SEND_SMS` granted,
the first `sms.send` still triggered EMUI's own confirmation ("allow Winnie to
send an SMS"), and the `sendTextMessage` call sat there until the owner tapped
it. The command then reported `Submitted the SMS to 15827082940. Delivery is not
confirmed by the app.` and the message arrived — twenty seconds after approval,
which looked like a slow carrier and was the dialog.
Two consequences worth keeping: the command's success means *the platform let
the send leave the app*, not that anything was delivered, and the delay is the
owner's tap, not the transport. Verify a send from the receiving handset; the
app's own message list cannot show it, because `SmsManager` never writes the
sent row into `content://sms` (`WRITE_SMS` is system-only).
