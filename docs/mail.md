# Mail: a mailbox the owner configures, a cache the rest of Winnie reads

Status: **implemented and verified end to end**, 2026-11-02. The owner's 126
account (`ninedoors@126.com`) is configured through the API and the console
Settings page, sends over SMTP, and is mirrored into the local cache: the first
sweep read **24 folders / 664 headers in 3.6 s**, the second read **0 new
messages in 2.8 s**, and the assistant answered a real chat turn from that cache
with `search_mail`. Last updated: 2026-11-02.

The package is `packages/winnie-mail` (`winnie-mail`); the host mounts it as the
`mail` capability, builds the proactive e-mail channel from it, and hands it to
the assistant as a **read-only data source**. The plan this implements is in
[proactive.md](./proactive.md); the process inventory is in
[operations.md](./operations.md).

## What it is for

Mail is two different things for Winnie, and they have different rules:

- **A channel for sending.** The proactive engine's `email` rung needs an SMTP
  account to reach the owner when push and ntfy are not enough.
- **A data source for reading.** The assistant and the console need to answer
  "what did that confirmation say", so recent mail is mirrored into the local
  database and queried on demand. No model may *send* mail — see
  [What it does not do](#what-it-does-not-do).

The configuration lives in **user settings**, not in an environment variable: one
account per Winnie user, editable at `/settings` in the console or over the API,
and read fresh on every operation. The proactive `email` rung is registered only
when a saved account can send (or the environment fallback below is configured),
so an empty form does not add a channel that fails on every item. The old
`proactive.channels.email` environment settings remain only as a fallback for a
deployment that has not filled in the form, so an existing setup keeps working.

## Where the settings live

```text
data/mail/<user_id>.yaml      # mode 0600, directory 0700
```

YAML, not a database row: the file holds a plaintext mailbox password, the
project already keeps credentials this way (`data/gateway-keys.yaml`,
`packages/winnie-llm/.env`), and a file needs no migration. `data/mail/` is in
`.gitignore`; check that before adding a second credential file, because this one
was not ignored when it was created and `git status` would happily offer to
commit the password.

The write is atomic: the store writes a temporary file in the same directory,
`chmod 0600`, then `os.replace`. A user id is validated against
`^[A-Za-z0-9._-]{1,64}$` before it becomes a path component.

What is stored:

```yaml
enabled: true
address: ninedoors@126.com
display_name: Winnie
smtp:        {host, port, security, username, password, from_address, from_name}
receive:     {protocol: imap|pop3|none, host, port, security, username, password,
              use_send_credentials, folder}
max_messages: 20
timeout_seconds: 20
updated_at: 2026-11-02T14:37:19+00:00
```

`use_send_credentials` (the default) means one login for both directions — which
is the normal case for 126, QQ, Gmail and most hosted mailboxes. Turning it off
requires a separate receiving username and password, and then a missing receive
password is a configuration error rather than a fallback.

`folder` is only a hint. Sync visits **every** selectable folder the server
lists, because a confirmation is as likely to be filtered into a label as it is
to sit in the inbox; the setting is what a single-body fetch falls back to when a
caller names no folder.

## The cache: what is stored, and what the cursors mean

The settings file is the credential; the cache is the data. Two tables in the
application database (`data/winnie.db`), both scoped by `user_id` so one Winnie
user can never read another's mail:

| Table | Holds | Unique key |
|---|---|---|
| `mail_folders` | one row per folder: `display_name`, `selectable`, `uid_validity`, `last_uid`, `total`, `unread`, `last_synced_at`, `last_error` | `(user_id, protocol, name)` |
| `mail_messages` | one row per message: subject, sender, recipients, `message_id`, `date`, size, `unread`, `snippet`, and — once somebody opens it — `body`, `body_html` and `body_raw` | `(user_id, protocol, folder, uid)` |

Headers only. A sweep stores what the `HEADER` field carries, so 664 messages
cost a few hundred kilobytes; the body is downloaded on the first
`GET /api/v1/mail/messages/{id}` and kept, which is why opening a message once
takes ~0.3 s and every later open ~0.02 s.

The three body columns are the three tabs of the reading pane, and they arrive
in one download:

| Column | What it is | Cap |
|---|---|---|
| `body` | flattened text: the part `text/plain` if there is one, otherwise the HTML with its tags stripped | 20 000 chars |
| `body_html` | the sender's `text/html` part, verbatim | 500 000 chars |
| `body_raw` | the RFC 822 source, headers and all | 200 000 chars |

The caps are the parser's, not the column's. `NULL` in all three means "not
opened yet", and a row whose `body` exists without `body_raw` predates the tabs —
the service refetches it once instead of showing two empty panes.

**Opening a message marks it read.** The fetch selects the mailbox writable and
sends `UID STORE +FLAGS (\Seen)` after `BODY.PEEK[]`, because `PEEK` deliberately
does not set the flag; the cache row and the folder's `unread` count drop in the
same transaction, so the sidebar cannot keep claiming unread mail that has been
read. A server that refuses the `STORE` (read-only mailbox) still returns the
body: the flag is the server's business, the message is not lost. POP3 has no
such flag, so a message read there is read only in the cache.

**The cursor is per folder, not per account.** IMAP numbers messages per mailbox
with a 32-bit UID and tells you when that numbering is invalidated:

- `last_uid` is the highest UID the folder has ever shown. It never decreases, so
  a UID gap caused by a deletion does not rewind it.
- `uid_validity` is the server's promise that the UIDs still mean what they meant
  last time. A different value means the mailbox was rebuilt: the folder's cached
  messages are dropped and the window is fetched again, rather than matched
  against stale numbers.
- Deletions are found by `UID SEARCH ALL` (one line for the whole folder) and
  compared with the cached UIDs; the difference is removed.
- `\Seen` arrives on the same fetch, so `unread` is the server's state, not a
  guess.

POP3 has no folders, no flags and no UIDs — only a per-message `UIDL` hash, which
is stable for the life of the message on that server. The cursor is therefore the
set of cached UIDLs: a UIDL the server no longer lists is a deleted message, a
UIDL not in the cache is new, and everything reads as read because POP3 has no
seen flag.

`per_folder_limit` (default 300, `mail.per_folder_limit` in `config.yaml`) is the
window: the newest N messages of each folder, not the whole archive. The folder
row still records the server's `total`, so the console can say `已删除 3826`
while the cache holds the newest 300 of them. Headers are fetched in batches of
100 UIDs per `FETCH`, because a 300-message window at 20 per command is fifteen
round trips per folder; the owner's mailbox went from 12.8 s to 3.6 s for the
same 333 headers when the batch grew.

## Sync: one login for the whole mailbox

`POST /api/v1/mail/sync` walks every folder in one pass:

1. `LIST` the folders (one connection), decode the names, turn `\Noselect`
   entries into empty snapshots, and prune cached folders the server no longer
   offers.
2. Log in **once** and, for each folder, `SELECT` it, `SEARCH ALL`, fetch the
   headers of the window that is not cached yet, and refresh the flags of the
   cached window.
3. One `LOGOUT`, then merge each folder's snapshot into the cache.

The console runs one sweep when the Mail page opens, and the assistant's gateway
runs one when the cache is older than 15 minutes. Both are ordinary callers of
the same route; there is still no polling loop.

The single connection is not a micro-optimisation. The first implementation
logged in per folder: 24 TLS logins in 17 seconds, after which 126 refused every
new connection at the TCP level for several minutes —

```
imap connect failed: ConnectionRefusedError: [Errno 111] Connection refused
```

while `/dev/tcp/imap.126.com/993` still opened. One connection per sweep turned
17 s into 3 s and removed the refusal. It is also what lets one broken folder be
recorded in `mail_folders.last_error` without abandoning the other 23.

> **A FETCH answer starts with the sequence number, not the UID.** Asking for
> `(FLAGS BODY.PEEK[HEADER])` returns `* 3 FETCH (FLAGS (\Seen) ...)` where `3`
> is the position in the mailbox — storing that as the UID made the cache hold
> `1..N`. The next sweep then saw no overlap with the server's 1.3-billion-range
> UIDs, treated every cached message as deleted *and* new, and the delete pass
> removed exactly the rows the insert had just written (329 messages in, 0
> messages left). The item list must include `UID`, and the parser must read
> `UID (\d+)` from it rather than the leading number.
> `apps/api/tests/test_mail.py` fakes a server whose UIDs are deliberately not
> the sequence numbers (`101, 102, …`), and `test_syncing_twice_keeps_the_cache`
> is the regression test.

## The HTTP surface

All routes require a session and act on the caller's own settings.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/settings/mail` | The current view. Passwords are **never** in it; `password_set` says whether one exists. |
| `PUT` | `/api/v1/settings/mail` | Replace the non-secret fields and, optionally, a password. |
| `DELETE` | `/api/v1/settings/mail` | Forget the account and purge its cache. |
| `POST` | `/api/v1/settings/mail/test` | `{"target": "smtp"}` logs in and hangs up; `{"target": "receive"}` logs in and counts. Nothing is sent or read. |
| `POST` | `/api/v1/settings/mail/send` | `{"to"?, "subject"?, "body"?}` sends to the account itself unless `to` is given. |
| `POST` | `/api/v1/mail/sync` | Sweep every folder and return `{protocol, folders, fetched, errors, stats}`. |
| `GET` | `/api/v1/mail/folders` | Cached folders with `uid_validity`, `last_uid`, `total`, `unread`, `last_synced_at`, `last_error`, plus `stats`. |
| `GET` | `/api/v1/mail/messages?folder=&q=&unread=&limit=&offset=` | Cached messages, newest first. `q` matches subject, sender, recipients, snippet and any cached body. |
| `GET` | `/api/v1/mail/messages/{id}` | One message with `body`, `body_html` and `body_raw`; downloads it on first open, marks it read, and caches all three. `404` for an id that is not cached. |

The password rule in `PUT` is what makes a "leave it alone" form possible:
`password: null` (or the key omitted) keeps the stored secret, `password: ""`
clears it, any other string replaces it. The console sends `null` when its
password box is empty, so loading the page and saving it again cannot wipe the
credential.

Error mapping: a configuration or authentication problem is `400` — never `401`,
because the console treats `401` as "your session expired" and would sign the
owner out. A transport failure is `502`. Cache routes need the application
database, so a mail-only deployment (no `database` configured) answers `503`.

Timestamps on the wire carry the zone (`2026-11-02T14:37:59Z`). The tables store
naive UTC, and a bare `2026-11-02T14:37:59` was read as local time by the model
and by every human looking at it — the assistant called a 22:00 Beijing message
"14:00" until the serializer started appending `Z`.

Folder names arrive in modified UTF-7 (`&XfJT0ZAB-` is 已发送), so a message
carries both `folder` (the raw name, the cache key) and `folder_display` (the
label to show). The console and the assistant use the label; the raw name is for
filtering.

## The assistant's read-only tools

`search_mail` and `read_mail` are the whole surface — there is no send tool, and
a message body is data, never an instruction. The skill is offered only when a
mailbox is configured (`SkillManifest.available`), and it is `scheduled_safe`, so
a scheduled task may consult mail.

- `search_mail(query, folder?, unread_only?, limit?)` returns matching messages
  with the cache's age, which is what lets an answer say "as of the 23:14 sync".
- `read_mail(message_id)` opens one message, downloading its body if needed.
- Every time is rendered in the owner's zone with an explicit offset
  (`2026-11-02 22:00 UTC+08:00`), because the model cannot see a column type and
  will otherwise present UTC as local.

The adapter (`apps/api/src/winnie_api/mail_gateway.py`) refreshes the cache first
when it is older than 15 minutes, and ignores a refresh failure: a read-only tool
must answer from what it has rather than fail because the network blipped.

## Protocols

- **SMTP**, implicit TLS on 465 (`security: ssl`), STARTTLS on 587
  (`starttls`), or plain `none`. Message construction sets `Date`, a
  `Message-ID` with the account's own domain, and the display name; several
  recipients are split on commas.
- **IMAP**, implicit TLS on 993 by default. Folders are selected `readonly`,
  bodies are fetched with `BODY.PEEK[]` so reading a message does not mark it
  seen, headers with `BODY.PEEK[HEADER]`, and `\Seen` becomes the `unread` flag.
- **POP3**, implicit TLS on 995. One mailbox, no flags: the cursor is the `UIDL`
  set and everything reads as read.

`max_messages` clamps any limit a caller passes to the settings routes, and
`per_folder_limit` clamps the sweep.

### 126 (Coremail) needs the IMAP `ID` command

A plain `imaplib` login to `imap.126.com:993` succeeds and then every
`SELECT` fails:

```
imaplib.IMAP4.error: SELECT command error: BAD [b'Unsafe Login. Please contact kefu@188.com for help']
```

126 requires RFC 2971 `ID` before `SELECT`, but Python's `imaplib` does not
register that command, so it is rejected as unknown:

```
imaplib.IMAP4.error: command SEARCH illegal in state AUTH
```

The client registers it and sends it whenever the server advertises `ID`:

```python
imaplib.Commands.setdefault("ID", ("AUTH", "SELECTED"))
mail._simple_command(
    "ID", '("name" "winnie" "version" "0.1" "vendor" "winnie")'
)
```

Keep the tuple in `Commands` accurate: `("AUTH", "SELECTED")` is what makes the
command legal in the authenticated state. Registering `ID` with the wrong state
list fails with the same `illegal in state AUTH` message.

### Sending to yourself proves SMTP, not delivery

126 archives a message addressed to its own account in **Sent** and does not
loop it back to the INBOX. A self-send therefore yields
`receipt: smtp ninedoors@126.com` with nothing in the inbox, which looks like a
delivery failure and is not one. To prove delivery, send to another mailbox; to
prove the *account* works for sending, the SMTP login and `send_message` call
are the evidence. The cache makes this visible a second way: the self-send
appears in the **Sent** folder's window, not in INBOX. Details in
[LESSONS-api.md](./LESSONS-api.md#a-mailbox-you-send-to-yourself-proves-smtp-not-delivery-2026-11-02).

## Verification

Unit and API coverage: `apps/api/tests/test_mail.py` (34 tests) covers the store
(mode 0600, atomic file, unsafe user id), the password merge, SMTP header
construction and recipient splitting, IMAP `ID` + `PEEK` + decoding, POP3, the
incremental cursor (new uid only, deleted uid removed, `UIDVALIDITY` reset, body
on demand), the one-connection sweep with a folder the server refuses, the cache,
and every route including the `400` mapping. `apps/api/tests/test_mail_tools.py`
(10 tests) covers the two assistant tools, the timezone rendering, the folder
label, and the absence of a send tool:

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_mail.py tests/test_mail_tools.py -q
# 44 passed
```

The live-account check, which no fake can replace, is these calls with a session:

```bash
# Fill the form (password included; it is never returned).
curl -X PUT http://127.0.0.1:8000/api/v1/settings/mail \
  -H 'Authorization: Bearer <session>' -H 'Content-Type: application/json' \
  -d '{"enabled":true,"address":"ninedoors@126.com",
       "smtp":{"host":"smtp.126.com","port":465,"security":"ssl",
               "username":"ninedoors@126.com","password":"<authorization code>"},
       "receive":{"protocol":"imap","host":"imap.126.com","port":993,
                  "security":"ssl","use_send_credentials":true,"folder":"INBOX"}}'

curl -X POST http://127.0.0.1:8000/api/v1/settings/mail/test \
  -H 'Authorization: Bearer <session>' -H 'Content-Type: application/json' \
  -d '{"target":"smtp"}'
# -> {"ok":true,"target":"smtp","detail":"smtp smtp.126.com:465 ok"}

# First sweep: 24 folders, 664 headers at the 300-per-folder window.
curl -X POST http://127.0.0.1:8000/api/v1/mail/sync -H 'Authorization: Bearer <session>'
# -> {"protocol":"imap","folders":24,"fetched":329,"errors":[],
#     "stats":{"messages":329,"unread":0,"folders":24}}
# ...then a second sweep after the window grew to 300 added the rest:
# -> {"protocol":"imap","folders":24,"fetched":333,"errors":[],
#     "stats":{"messages":664,"unread":1,"folders":24}}

# Second sweep: nothing new, and the cache is unchanged.
curl -X POST http://127.0.0.1:8000/api/v1/mail/sync -H 'Authorization: Bearer <session>'
# -> {"protocol":"imap","folders":24,"fetched":0,"errors":[],
#     "stats":{"messages":329,"unread":0,"folders":24}}

# A body is fetched once, then served from the cache.
curl 'http://127.0.0.1:8000/api/v1/mail/messages?q=%E9%82%AE%E7%AE%B1&limit=3' \
  -H 'Authorization: Bearer <session>'
curl "http://127.0.0.1:8000/api/v1/mail/messages/$ID" -H 'Authorization: Bearer <session>'
# first open 0.288 s (network), second open 0.022 s (cache)
```

The console path was driven in headless Firefox against `/mail`. Opening the page
started a sweep on its own (`right after load: true` for the "Syncing…" state,
then `Last synced Nov 2, 2026, 11:37 PM` and `664 messages · 0 unread`), the
folder list showed every mailbox (`已删除 3826`, `已发送 4`, …), and opening the
SendCloud weekly report rendered the sender's markup inside the sandboxed frame
(`HTML*`, `frameChars: 57454`, `sandbox=""`, CSP
`default-src 'none'; img-src * data: …`), with the **Raw** tab showing
`Received: from ucmail246.sendcloud.org …`. Dark theme and a 390 px viewport were
measured rather than eyeballed: `data-bs-theme=dark`, `body` `rgb(43, 48, 53)`,
frame 304 px wide, no horizontal overflow, three tabs still visible, and no
console errors.

The assistant path was a real chat turn — *"我最近的邮件里有一封新设备登录提醒…"* —
which called `search_mail` and answered with the subject, the sender and
`2026-11-02 14:00（北京时间）`, matching the `2026-11-02 21:59:57` inside the
message body.

`packages/winnie-mail` is installed editable by `uv sync`; **restart the API**
after changing it. `uvicorn --reload` will not do it on its own, and even when a
reload happens a stale worker can answer on the port while serving old code —
check `/openapi.json` for the route you just added before believing a result
(see [LESSONS-api.md](./LESSONS-api.md)).

## What it does not do

- No scheduled polling loop. Sync runs when the console presses **Sync mailbox**
  or when the assistant's gateway finds the cache older than 15 minutes.
- The cache holds the newest 300 messages per folder, not the archive. Older mail
  is visible in the folder's `total` and nowhere else.
- No attachment download: an attachment is visible in the raw source and nowhere
  else. No message deletion.
- HTML is rendered, not sanitised. The frame is `sandbox`ed without
  `allow-scripts`/`allow-same-origin` and carries a CSP, so a sender's script
  cannot run — but remote images and fonts do load, and a tracking pixel is
  still a request. Nothing in the message can reach the console's own DOM.
- No model-initiated sending. The assistant gets `search_mail` and `read_mail`
  only: outward communication stays behind a route the owner triggers, the same
  rule the proactive engine follows for its own channels.
- No HTML rendering. Message bodies are converted to text, so a mail client is
  still the place to read a designed newsletter.
