# Push: reminders without polling

Status: first implementation. The server stream, the assistant tool, the phone
command integration and the Android foreground service are implemented and
tested; a run on a physical phone is still pending. Last updated: 2026-11-01.

## 1. Goal

Winnie should be able to reach the owner when something happens, without the
phone asking every minute:

- a scheduled task produces its brief;
- the owner asks the assistant to remind them;
- a confirmed calendar change or SMS finishes on the phone.

That needs a channel the server can write to and the phone can receive, and a
phone-side process that stays alive between user sessions. Success means: a
notification published on the server appears on the phone within seconds while
the connection is up, and still arrives on the next connect when it is not.

## 2. Why a self-hosted socket instead of FCM

| Option | Benefit | Cost here |
|---|---|---|
| **Firebase Cloud Messaging** | OS-level delivery, best battery, wakes a sleeping device | Requires Google Play Services on the phone, a Firebase project and server credentials; unreliable or absent on many China-market ROMs; the payload passes through Google |
| **UnifiedPush / ntfy** | No Google, a real push distributor | A second app and service to install and keep running; another account and trust boundary |
| **Long-lived WebSocket in a foreground service** (chosen) | Self-contained, no vendor, no new account; works on a sideloaded APK without GMS | A persistent notification, battery use, and no delivery while the process is force-stopped or the phone is in deep Doze |
| **WorkManager polling only** | Simplest | A reminder can be up to the poll interval late; that is not a reminder |

This is a personal, self-hosted assistant whose server is already reachable from
the phone, so the socket is the option that adds no third party. The fallback is
the durable backlog plus the existing periodic sync: a reminder is never lost,
only delayed.

## 3. Architecture

```
publisher                           PushService                         Android
─────────                           ───────────                         ───────
assistant notify_user  ─┐           insert row (seq, expires_at)          WinniePushService
scheduled task result  ─┼──────────►  ├── commit                          (foreground, specialUse)
phone command approved ─┤             └── hub.publish ──► WS /push/ws ──►  decode frame
phone command result   ─┘                  │                              ├── notification
                                           └── backlog on reconnect       └── sync event
                                                                              └─► WorkManager
```

The database row is the source of truth; the WebSocket is a delivery
acceleration. That ordering is what makes the feature robust: a publish that
happens while the phone is offline is a backlog row, not a lost event.

## 4. Event model

`push_notifications`:

| Column | Meaning |
|---|---|
| `seq` | Monotonic integer primary key; the delivery cursor |
| `id` | Public UUID used by the REST API |
| `user_id` | Owner; the stream is authenticated as this account |
| `device_id` | Null for every device of the user, set for a targeted control message |
| `kind` | `notification`, `sync` or `phone_command` |
| `title`, `body` | The text shown on the phone (title ≤200, body ≤4000) |
| `data` | Optional JSON for the client or a future deep link |
| `created_at`, `expires_at` | Naive UTC; an expired event is never delivered |

Kinds:

- **`notification`** — a user-facing alert. Shown in the alert channel.
- **`sync`** — a control message. The phone runs a data sync immediately instead
  of showing anything. It expires after five minutes because a stale "sync now"
  is noise.
- **`phone_command`** — the outcome of a confirmed phone write. Shown as an
  alert; the detail also stays in the chat.

Retention deletes expired events and anything older than
`push.retention_days` (default 30). The prune pass runs hourly while the API is
up and is also callable from the service.

## 5. Protocol

### WebSocket

```
GET /api/v1/push/ws?after=<seq>&device_id=<uuid>
Authorization: Bearer <token>        (or ?token= for clients without headers)
```

The handshake is authenticated with the account session. On connect the server
sends:

1. every unexpired event with `seq > after` that is either broadcast or targeted
   at this device, oldest first, as `{"type":"event", ...}`;
2. `{"type":"ready","last_seq":N}` once the backlog is drained;
3. live events as they are published;
4. `{"type":"ping"}` on the heartbeat interval.

The client may send `{"type":"ping"}` and receives `{"type":"pong"}`. The
delivery cursor is the `seq` of the last event shown. The client persists it
before displaying the notification, so a reconnect neither loses nor repeats an
event (an event at or below the cursor is ignored).

`after` is a hint, not an authorization boundary: the server only ever returns
events for the authenticated user, and only the ones addressed to the device id
(or broadcast).

### REST fallback

```
GET  /api/v1/push/notifications?after_seq=&device_id=&limit=
GET  /api/v1/push/status
POST /api/v1/push/test            {"title": "...", "body": "..."}
```

`/notifications` returns the same backlog as the socket. It exists for the
foreground service's cold start and for a future periodic pull when the service
cannot run. `/status` reports open connections for the account, the pending
count and the newest `seq`. A fresh install uses that cursor to start about 50
events before the present instead of replaying the whole retention window as
notifications. `/test` publishes a notification to the caller; the app's "Send
test notification" button uses it as the end-to-end check.

## 6. Where events come from

| Source | Kind | What it does |
|---|---|---|
| Assistant `notify_user` tool | `notification` | A proactive reminder or a result the owner asked to be told about |
| Scheduled task result | `notification` | Every run of a scheduled task pushes its brief unless `assistant.tasks.notify_results` is false |
| Phone command approved | `sync` | Wakes the phone to claim and execute the command now, instead of waiting for the periodic sync |
| Phone command result | `phone_command` | Tells the owner that the calendar change or SMS finished, or why it failed |
| Gold price watch | `notification` | A move past the hourly or daily threshold, with the price and percentage |

The assistant's `notify` skill is available in scheduled task runs (a brief is
useless if nobody sees it) but the write tools of `phone_write` are not. The
instructions tell the model not to notify for every reply and never to put
credentials or transcripts on a lock screen.

## 7. The Android client

### Foreground service

`WinniePushService` holds one OkHttp WebSocket open:

- **Foreground service, type `specialUse`.** The standard FGS types do not cover
  "a self-hosted notification socket", and `dataSync` is capped at six hours a
  day on Android 15, which would silently stop delivery. `specialUse` requires a
  declaration (done) and a Play Console justification (this app is sideloaded).
- **Persistent notification.** Low importance, always visible, with a **Stop**
  action. That is the visible cost of the connection and the reason it is
  opt-in.
- **Reconnect with exponential backoff** from 2 s to 60 s, reset on open. A 401
  shows "session expired"; a 403 shows "device revoked"; both keep retrying
  slowly because the fix is on the owner's side.
- **`START_STICKY` and a boot receiver.** If Android kills the process it
  restarts the service; after a reboot or an app update the receiver starts it
  again when enabled. Some vendors block a background foreground-service start;
  the failure is recorded and the next app open retries.
- **A `sync` event** goes to `PhoneSyncScheduler.requestSync`, which is what
  makes a confirmed write take effect in seconds.
- **Notifications** use two channels: `winnie_background` (low, the service) and
  `winnie_alerts` (high, the reminders), so a user can silence the first without
  losing the second. Tapping opens the app.

### Permissions and settings

- `POST_NOTIFICATIONS` (Android 13+) is requested when the owner enables the
  connection. If denied, the service still runs, but alerts are invisible except
  in the Task Manager; the screen says so and links to settings.
- Battery optimization exemption is offered with
  `ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` (sideload only; Play restricts
  it). Without it, some ROMs kill the connection.
- The **Alerts & background** screen shows the switch, the live connection
  state, the last event, the server's pending count and connection count, the
  two permission states, a **Send test notification** button, and an honest note
  that vendors may still kill background services.

The connection is stopped on sign-out; its cursor is kept for the next sign-in.

## 8. Configuration

```yaml
push:
  enabled: true
  retention_days: 30
  max_backlog: 200
  heartbeat_seconds: 25
  max_queue: 100
  prune_interval_seconds: 3600

assistant:
  tasks:
    # Push each scheduled task's result as a notification.
    notify_results: true
```

`push.enabled` is off in the built-in defaults and on in the shipped
`config.yaml`. Disabling it removes the capability, the `notify` skill and the
command wake-ups; the phone shows the connection as unavailable.

## 9. Security and privacy

- **No third party sees the notification.** The content goes from the owner's
  server to the owner's phone; that is the main reason for this design.
- **The server database is not encrypted at rest.** Notification titles and
  bodies live in the same database as everything else. They should be treated
  as visible to anyone with the database file.
- **Transport.** The socket is `ws://` when the API is HTTP. A bearer token and
  the notification text then cross the network in clear; terminate TLS before
  using this outside a trusted network. `wss://` works today with no client
  change.
- **Lock-screen exposure.** Alerts can show message text on a locked screen.
  The assistant is told to keep bodies short and free of secrets; the owner can
  set the alert channel to "hide sensitive content" in Android settings, and
  should not ask for notification of secrets.
- **Authorization.** Every event belongs to an account; the socket authenticates
  as that account. A device id only filters; it cannot read another account's
  events. `/push/test` publishes only to the caller.
- **One process, one hub.** The fan-out is in-process. Two uvicorn workers would
  have two hubs, and a publish would reach only the connections on the same
  worker. Run one worker (the default), or add Redis pub/sub before scaling out.
  The backlog keeps delivery correct either way; only latency is affected.

## 10. Limits and known gaps

- A device run has not happened. Doze, vendor task killers, the FGS type and
  notification permission flows are verified by unit tests and a successful
  build, not by a handset.
- No push when the process is force-stopped; only the backlog on the next open.
  No way around that without a system push service.
- No delivery receipt. The cursor proves the client saw an event, not that a
  person read it.
- No notification actions (reply, snooze, "done") and no deep link into a
  specific conversation; tapping opens the app.
- No quiet hours, grouping or per-category preferences beyond Android's channel
  controls.
- No iOS client and no Web Push for a browser.
- Meetings, news and the weather watcher do not publish push events yet; the
  hooks exist (`PushService.notify`) and the weather watcher's webhook is the
  natural next source. The gold watcher already publishes through the
  assistant's notifier.
- Recurring reminders are scheduled tasks, not a separate reminder model.

## 11. Verification

```bash
# Server: backlog, live socket, auth, targeting, expiry, phone events, tool
cd apps/api && .venv/bin/python -m pytest tests/test_push.py \
  tests/test_notify_tools.py -q

# Android: frame parsing, routing, preferences, REST contract, screen
cd apps/android && ./gradlew :app:testDebugUnitTest

# End to end against a real uvicorn process: publish, receive over a real
# WebSocket, approve a command, receive the sync and result events, replay the
# backlog after a reconnect (see the run recorded in the commit).
```
