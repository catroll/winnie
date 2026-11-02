# Mail: one mailbox per account, for sending and receiving

Status: **implemented and verified end to end**, 2026-11-02. The owner's 126
account (`ninedoors@126.com`) is configured through the API and the console
Settings page; `POST /api/v1/settings/mail/test` answered
`{"ok": true, "detail": "smtp smtp.126.com:465 ok"}` and
`{"ok": true, "detail": "imap imap.126.com:993 46 messages"}`, and
`GET /api/v1/settings/mail/messages` returned the ten newest INBOX messages with
decoded subjects and senders. Last updated: 2026-11-02.

The package is `packages/winnie-mail` (`winnie-mail`); the host mounts it as the
`mail` capability and builds the proactive e-mail channel from it. The plan this
implements is in [proactive.md](./proactive.md); the process inventory is in
[operations.md](./operations.md).

## What it is for

Two things need a mailbox and until now only the second had one:

- **Sending** — the proactive engine's `email` rung needs an SMTP account to
  reach the owner when push and ntfy are not enough.
- **Receiving** — the system needs to read what arrives, so a reply can be
  noticed, a verification link retrieved, or an inbox shown in the console.

The configuration therefore lives in **user settings**, not in an environment
variable: one account per Winnie user, editable at `/settings` in the console or
over the API, and read fresh on every operation. The proactive `email` rung is
registered only when a saved account can send (or the environment fallback below
is configured), so an empty form does not add a channel that fails on every
item. The old
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

## The HTTP surface

All routes require a session and act on the caller's own settings.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/settings/mail` | The current view. Passwords are **never** in it; `password_set` says whether one exists. |
| `PUT` | `/api/v1/settings/mail` | Replace the non-secret fields and, optionally, a password. |
| `DELETE` | `/api/v1/settings/mail` | Forget the account (removes the file). |
| `POST` | `/api/v1/settings/mail/test` | `{"target": "smtp"}` logs in and hangs up; `{"target": "receive"}` logs in and counts. Nothing is sent or read. |
| `POST` | `/api/v1/settings/mail/send` | `{"to"?, "subject"?, "body"?}` sends to the account itself unless `to` is given. |
| `GET` | `/api/v1/settings/mail/messages?limit=` | The newest messages, read-only. |

The password rule in `PUT` is what makes a "leave it alone" form possible:
`password: null` (or the key omitted) keeps the stored secret, `password: ""`
clears it, any other string replaces it. The console sends `null` when its
password box is empty, so loading the page and saving it again cannot wipe the
credential.

Error mapping: a configuration or authentication problem is `400` — never `401`,
because the console treats `401` as "your session expired" and would sign the
owner out. A transport failure is `502`; a missing `MailService` is `503`.

## Protocols

- **SMTP**, implicit TLS on 465 (`security: ssl`), STARTTLS on 587
  (`starttls`), or plain `none`. Message construction sets `Date`, a
  `Message-ID` with the account's own domain, and the display name; several
  recipients are split on commas.
- **IMAP**, implicit TLS on 993 by default. The mailbox is selected `readonly`,
  bodies are fetched with `BODY.PEEK[]` so reading the preview does not mark
  anything seen, and `\Seen` becomes the `unread` flag.
- **POP3**, implicit TLS on 995. POP3 has no folders and no flags, so the
  preview is the newest N messages by `UIDL` and everything reads as unread.

`max_messages` clamps any `limit` a caller passes, so a console page cannot ask
for an entire inbox.

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
are the evidence. Details in
[LESSONS-api.md](./LESSONS-api.md#a-mailbox-you-send-to-yourself-proves-smtp-not-delivery-2026-11-02).

## Verification

Unit and API coverage: `apps/api/tests/test_mail.py` (20 tests) covers the store
(mode 0600, atomic file, unsafe user id), the password merge, SMTP header
construction and recipient splitting, IMAP `ID` + `PEEK` + decoding, POP3, and
every route including the `400` mapping:

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_mail.py -q
```

The live-account check, which no fake can replace, is four calls with a session:

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

curl -X POST http://127.0.0.1:8000/api/v1/settings/mail/test \
  -H 'Authorization: Bearer <session>' -H 'Content-Type: application/json' \
  -d '{"target":"receive"}'
# -> {"ok":true,"target":"receive","detail":"imap imap.126.com:993 46 messages"}

curl 'http://127.0.0.1:8000/api/v1/settings/mail/messages?limit=3' \
  -H 'Authorization: Bearer <session>'
```

The console path was driven in headless Firefox: the Settings page loads the
stored account, "Test sending" and "Test receiving" print the server's answer in
the alert, and "Load recent messages" fills the table with decoded subjects and
senders.

`packages/winnie-mail` is installed editable by `uv sync`; **restart the API**
after changing it, because `uvicorn --reload` watches `apps/api` only.

## What it does not do

- No scheduled polling. Receiving happens when the console or a caller asks.
- No attachment download and no message deletion; the preview carries subject,
  sender, date, snippet and up to 4000 characters of body.
- No model-initiated sending. The gateway/assistant does not get a "send mail"
  tool: outward communication stays behind a route the owner triggers, the same
  rule the proactive engine follows for its own channels.
- No HTML rendering. Message bodies are converted to text, so a mail client is
  still the place to read a designed newsletter.
