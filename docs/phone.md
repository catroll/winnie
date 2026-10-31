# Phone data: sync, query and deferred writes

Status: first implementation. The server contract, the Android client and the
assistant tools are implemented and unit-tested; a run on a physical phone is
still pending. Last updated: 2026-11-01.

## 1. Goal

The assistant should be able to answer questions about the owner's phone — what
is on their calendar, who called, what was said in a text, what an app
notified, where they were, what is installed — and to change the calendar or
send a text when the owner asks for it.

That needs two directions:

- **Read.** The phone uploads what it can read to the server, where it becomes
  searchable and quotable by the assistant.
- **Write.** The server cannot reach a phone, so a write is a command the phone
  claims on its next sync. The owner confirms it in chat first; the assistant
  never executes an unconfirmed write.

Success for this slice means:

1. Enabling a scope on the phone makes its records available to the assistant
   within one sync.
2. A question about a call, a text, an event or a notification is answered from
   the synced copy, with the sync time stated.
3. "Put it in my calendar" produces a proposal, an explicit confirmation and a
   command the phone executes, with the result reported back.
4. Deleting the server copies from the phone really removes them, and turning a
   scope off stops future uploads.

## 2. What is synced, and what is not

| Kind | Source | Uploaded | Default retention |
|---|---|---|---|
| `calendar` | `CalendarContract.Events` | Title, description, location, start/end, all-day, timezone, RRULE, organiser, status, calendar display name | 730 days |
| `call` | `CallLog.Calls` | Number, cached name, direction, time, duration, SIM account, new flag | 730 days |
| `message` | `content://sms` | Thread, address, direction, body, subject, time, read flag | 365 days |
| `contact` | `ContactsContract` | Display name, numbers, emails, organisation, starred | snapshot, no expiry |
| `notification` | `NotificationListenerService` | Package, app label, title, text, category, time, clearable flag | 14 days |
| `location` | `LocationManager` | One fix per sync: latitude, longitude, accuracy, altitude, speed, provider | 30 days |
| `app` | `PackageManager` | Package, label, version, version code, system/enabled flags, install/update time | snapshot, no expiry |
| `device` | `Build`, battery, storage, network, telephony, accounts | Name, model, OS, app version, timezone and an extensible `info` object | current snapshot |

Deliberately **not** collected:

- MMS attachments, SMS pictures and any media or file content. Text only.
- Call recordings, voicemail audio.
- Clipboard, keystrokes, health data, screen content.
- Per-app usage statistics or foreground-app history.
- Contact photos.

The last table column is a retention default; it is deployment configuration,
not a promise about the phone.

## 3. Architecture

```
Android app                                  FastAPI host
───────────                                  ────────────
PhoneSyncWorker (WorkManager)                /api/v1/phone/*
  ├── content observers (SMS, calls,         └── winnie-phone
  │   calendar, contacts)                        ├── PhoneService
  ├── NotificationListenerService ──► buffer      │   ├── devices + sync states
  ├── sources: calendar, call log, SMS,           │   ├── calendar/call/message/contact
  │   contacts, apps, device, location            │   ├── signals (notification/location/app)
  └── command runner                              │   ├── commands (propose/approve/claim)
        ▲                                         │   └── retention
        │  GET approved commands                 │
        └────────── POST results ────────────────┘
                                                  assistant tools
                                                  (`phone`, `phone_write`)
                                                  + phone digest in the prompt
```

`winnie-phone` owns the schema, the merge rules and the queries. It never
imports the host and the host never writes phone rows. The assistant declares a
`PhoneGateway` protocol, and `apps/api/src/winnie_api/phone/gateway.py` adapts
the service to it, including the "whose phone?" decision.

## 4. Sync contract

All routes live under `/api/v1/phone` and require the same bearer session as
the rest of the API.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/devices` | Register or update a device (upsert by `device_key`) |
| `GET` | `/devices` | List the account's devices with per-kind sync states |
| `GET` | `/devices/{id}` | One device |
| `POST` | `/devices/{id}/sync` | Upload one batch of one kind |
| `GET` | `/devices/{id}/ids?kind=` | Ids the server holds, for a client-side snapshot diff |
| `GET` | `/devices/{id}/commands` | Claim approved commands (marks them dispatched) |
| `POST` | `/devices/{id}/commands/{cid}/result` | Report a command result |
| `DELETE` | `/devices/{id}` | Revoke the device |
| `POST` | `/devices/{id}/restore` | Undo a revocation |
| `DELETE` | `/devices/{id}/data` | Delete every synced record, keep the registration |
| `GET` | `/summary` | Devices, counts, staleness, pending commands |
| `GET` | `/calendar`, `/calls`, `/messages`, `/contacts`, `/notifications`, `/locations`, `/apps` | Typed queries |
| `GET` | `/search?q=&kinds=&since=&until=` | Cross-kind search, merged by time |
| `POST` | `/commands` | Propose a write |
| `POST` | `/commands/{id}/approve`, `/cancel` | Approve or cancel a proposed write |
| `GET` | `/commands?status=` | Command history |

A sync request carries **one kind**:

```json
{
  "kind": "calendar",
  "mode": "full",
  "cursor": "",
  "since": "2026-05-05T00:00:00Z",
  "until": "2027-11-01T00:00:00Z",
  "items": [{ "external_id": "501", "title": "Dentist", "starts_at": "..." }],
  "deleted": ["499"],
  "device": { "name": "Pixel 8", "scopes": ["calendar"], "info": {} }
}
```

Rules the server enforces:

- **One kind per request, at most `phone.max_batch_items` items** (default 200).
  Larger histories are paged by the client.
- **`mode: "full"`** means the items are the complete view for that kind, so
  rows the batch omits are marked deleted. It is accepted for `calendar`,
  `contact`, `app` and `device`; message and call history must report deletions
  explicitly because a full snapshot would need one unbounded request.
- **`since`/`until` scope a full snapshot.** A windowed calendar snapshot only
  infers deletions for rows whose start falls inside the window, so syncing the
  next 12 months never deletes last year.
- **Upserts are keyed by `(device, external_id)`.** Re-uploading the same batch
  after a timeout updates rows and does not duplicate them.
- **Deletes are soft** (`deleted_at`); queries exclude them and retention
  eventually removes the tombstones. This prevents a later full snapshot from
  resurrecting a record the phone deleted.
- **`since`/`until` on `/ids`** return only the ids inside the window, which is
  what a paged calendar snapshot diffs against.

Timestamps are normalised to naive UTC on write and returned with a UTC offset.
Response:

```json
{"kind": "calendar", "accepted": 2, "deleted": 1, "cursor": "",
 "server_time": "2026-11-01T06:00:00Z", "interval_minutes": 30}
```

`interval_minutes` is the sync hint the phone applies to its periodic worker.

### Device identity

The app generates a random `device_key` once and keeps it. The server upserts on
`(user_id, device_key)` and stores its own device id, which the app persists.
Re-registering after a server-side wipe reuses the same device. A **revoked**
device is refused at registration, so revocation is sticky until an explicit
`/restore`.

### Commands

A command is `proposed` → `approved` → `dispatched` → `succeeded`/`failed`, or
`cancelled`/`expired`. A proposed command is created with
`expires_at = now + phone.command_ttl_seconds` (default 900). Approval only
works while it is unexpired. A device claims at most
`phone.max_commands_per_poll` approved commands per poll.

Supported kinds and payloads:

| Kind | Payload |
|---|---|
| `calendar.create` | `title`, `starts_at`, optional `ends_at` (start + 1h), `all_day`, `location`, `description`, `calendar_id`, `calendar_name`, `timezone` |
| `calendar.update` | `event_id`, any of `title`, `starts_at`, `ends_at`, `location`, `description` |
| `calendar.delete` | `event_id` |
| `sms.send` | `address`, `body` |

## 5. The write path and what confirmation buys

```
owner: "move my 3pm to 4"
assistant: phone_calendar_update(...)      → server stores a proposed command
assistant: "Update 'Dentist' to 16:00-17:00? Confirm and I'll queue it."
owner: "yes"
assistant: phone_confirm(id)               → approved
phone (next sync): claims it, writes the event, reports the result
```

Consequences that must not be smoothed over in the assistant's wording:

- Approval is **not** execution. The command runs when the phone next syncs,
  which can be minutes away, and the result appears on the phone.
- A proposal that is never confirmed expires and is never dispatched.
- An approved command whose phone never syncs again just sits there; the
  assistant reports it as pending.
- `sms.send` submits the message to the carrier. The app reports "submitted",
  not "delivered"; there is no delivery-receipt tracking.
- Calendar writes target a writable calendar (preferred name, else the primary
  writable one). The app refuses with a clear error when there is none.

## 6. Assistant integration

Two skills, so a scheduled brief can read phone data without being able to
write:

| Skill | Tools | Scheduled turns |
|---|---|---|
| `phone` | `phone_status`, `phone_search`, `phone_calendar`, `phone_calls`, `phone_messages`, `phone_notifications`, `phone_location`, `phone_apps` | allowed |
| `phone_write` | `phone_calendar_create`, `phone_calendar_update`, `phone_calendar_delete`, `phone_sms_send`, `phone_confirm`, `phone_cancel` | excluded |

Every answer carries the last sync time, and the tool output names the device.
Phone text is treated like any other untrusted content: an SMS or a
notification is data written by someone else, never an instruction.

The system prompt also carries a compact digest for the current turn: the last
sync, the next 24 hours of calendar events, unread SMS and today's missed
calls. It is capped at about 1,200 characters and omitted entirely when no
phone has synced, so a deployment without the feature pays nothing.

The assistant reads **one owner's** phone. With accounts enabled, that is
`phone.owner_user` when set, otherwise the only active account. A multi-account
instance must name the owner; otherwise the phone tools report why they are
unavailable instead of guessing.

## 7. The Android client

### Permissions

| Scope | Runtime permissions | Special access |
|---|---|---|
| Calendar | `READ_CALENDAR`, `WRITE_CALENDAR` | — |
| Call log | `READ_CALL_LOG` | — |
| SMS | `READ_SMS`, `SEND_SMS` | — |
| Contacts | `READ_CONTACTS` | — |
| Notifications | — | Notification listener access |
| Location | `ACCESS_FINE_LOCATION`, `ACCESS_COARSE_LOCATION` | `ACCESS_BACKGROUND_LOCATION` for background fixes |
| Apps | `QUERY_ALL_PACKAGES` (normal) | — |
| Device | `GET_ACCOUNTS` for the account list (optional, guarded) | — |

`READ_SMS`, `SEND_SMS` and `READ_CALL_LOG` are allowed by the OS once granted,
but **Google Play restricts them** for apps that are not the default SMS
handler or a declared core-function exception. This build is for personal
sideloading. That is a distribution decision, not a code defect.

### Sync mechanics

- **WorkManager** periodic work, default 30 minutes, never below Android's
  15-minute minimum. The interval follows the server hint and is updated after
  each sync.
- **Content observers** on the SMS, call-log, calendar and contacts providers
  request a debounced one-shot sync while the app process is alive.
- **The notification listener** appends to a bounded JSON-lines queue in the
  app's private storage and requests a sync. The queue drops the oldest entries
  beyond 2,000; a crash between upload and ack re-sends a few items, which the
  server deduplicates.
- **Location** asks for one fix per sync, reusing a last-known fix younger than
  five minutes. A background fix needs "allow all the time" access; a refusal
  skips the fix instead of failing the sync.
- **A sync run isolates scopes**: one provider throwing records an error and
  the others still upload. Only an expired session or a transport failure stops
  the run.

### SMS and calendar accuracy notes

- Only the `sms` provider is read. MMS parts are not.
- SMS edits and deletions made in another app are not visible through the
  `_ID` cursor. The server keeps the last uploaded copy until retention removes
  it. The app does not currently offer message deletion, so it reports no
  deletions either.
- Recurring calendar events are synced as one `Events` row with its `RRULE`,
  not as expanded `Instances`. This keeps the id used for update/delete equal to
  the id the assistant sees, at the cost of not listing every occurrence.
- SMS sending is fire-and-forget after `SEND_SMS`: the result means "submitted
  to the carrier".

### UI

`Phone data` on the home screen is the consent surface. It contains the privacy
notice, a master switch, one switch per scope with its permission state, the two
special-access rows, a manual sync, the last sync and error, server-side counts,
and a confirmed "Delete data on server" action. Nothing is enabled by default.

## 8. Privacy and security

- **The server database is not encrypted at rest.** It is SQLite or PostgreSQL
  under `data/`, with the same protection as meeting transcripts and assistant
  conversations: filesystem permissions. The phone screen says so. A deployment
  that stores someone's SMS and location should encrypt the volume.
- **Transport.** The app still permits cleartext HTTP for a LAN server, as the
  meeting MVP does. Syncing SMS over a public network without TLS is a real
  leak; terminate TLS first. The phone data screen repeats this.
- **Authentication.** The same bearer session as the rest of the API. Every
  route is scoped to the authenticated user, and a device id in a path is
  checked against that user's devices. Another account's device id is a 404.
- **Sessions expire.** The default session lifetime is 30 days. Background sync
  then records "session expired" and stops uploading; there is no separate
  long-lived device token yet.
- **Retention** runs on every sync. Per-kind windows are configuration
  (`phone.retention`); `0` keeps everything. Contacts and apps are snapshots
  and have no natural age, so their windows prune tombstones only.
- **Deletion.** `/devices/{id}/data` hard-deletes every record for the device
  and its sync states; the app resets its cursors so a later sync re-uploads.
  Revocation blocks the device until restored.
- **Logs** record the device id, kind and counts, never a message body or a
  phone number.
- **Notifications may contain one-time codes.** They are stored and searchable
  by design; the short default retention (14 days) limits the exposure.
- **The phone is not a trusted code path for writes.** The server validates the
  command payload before queueing it, but the phone is what actually inserts
  the event or sends the message; a modified app can do anything its
  permissions allow. This protects the owner from the assistant, not from
  themselves.

## 9. Configuration

`apps/api/config.yaml`:

```yaml
phone:
  enabled: true
  owner_user: ""            # required when the instance has more than one account
  sync_interval_minutes: 30 # hint only; Android's floor is 15
  max_batch_items: 200
  max_text_chars: 8000
  command_ttl_seconds: 900
  max_commands_per_poll: 10
  retention:
    calendar_days: 730
    call_days: 730
    message_days: 365
    contact_days: 0
    notification_days: 14
    location_days: 30
    app_days: 0
```

`phone.enabled` is off in the built-in defaults and on in the shipped
`config.yaml`. A programmatic `Settings()` (tests, embedders) does not create a
phone database unless it asks for one.

## 10. Limits and known gaps

- A device run on a real phone has not happened yet. Permission flows, the
  notification listener, background location and the vendors' calendar
  providers are verified by unit tests and a successful APK build, not by a
  handset.
- No MMS, no attachments, no media.
- No push channel: writes wait for the next sync. A lightweight command poll
  (separate from the heavy data sync) would shorten that.
- No delivery receipts for SMS.
- No streaming or long-poll; polls are one request per kind per page.
- No server-side full-text index. Search is `LIKE` over small phone datasets;
  it will need FTS if a device accumulates years of notifications.
- No per-device scope enforcement on the server: the phone decides what it
  uploads, and the server accepts any kind. The scopes are context for the
  assistant, not an authorization boundary.
- The admin console has no phone page yet; the API is the way to inspect and
  delete data from a browser.

## 11. Verification

```bash
# Server: sync, queries, retention, commands, isolation, migrations, tools
cd apps/api && .venv/bin/python -m pytest tests/test_phone.py \
  tests/test_phone_tools.py tests/test_migrations.py -q

# Android: wire contract, cursor mapping, preferences, notification buffer,
# and a Robolectric render of the consent screen
cd apps/android && ./gradlew :app:testDebugUnitTest

# The APK
make android
```
