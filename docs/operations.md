# Operations: services and lifecycle

This page is the authoritative list of processes the project can run, the
addresses they bind, and how to manage them locally. The API configuration
lives in `apps/api/config.yaml`; this page does not repeat it. The role model and
the admin/operator route inventory are in [admin-api.md](./admin-api.md).

## Service inventory

| Service | Default bind | Role | Needed for |
|---|---|---|---|
| **API** | `[::]:8000` | Model gateway, accounts, meetings, assistant, index proxy, admin API | Always |
| **Document index** | `127.0.0.1:8790` | `winnie-index` HTTP API and MCP: search, read, status, admin endpoints | Knowledge recall, console Knowledge page |
| **Admin console** | `127.0.0.1:5173` | Vite dev server for the AdminLTE console; proxies API prefixes to the API | Browser management UI |
| **arq worker** | no listening port | Background meeting jobs and retention cleanup | Only when `jobs.backend: arq` |
| **Redis** | `127.0.0.1:6379` | arq queue and job state | Only with the arq worker |
| **PostgreSQL** | `127.0.0.1:5432` | Production database | Optional; SQLite is the local default |
| **Reverse proxy** | `:443` | TLS, static admin files, API/index same-origin routing | Production |

Notes:

- The **model gateway is not a separate process**. It is a capability mounted
  inside the API, so the same port serves `/{route}/...` and the Winnie routes.
  Its route table lives in that process, so one API worker owns the live table;
  a second worker sees a console change on its next restart. Gateway keys are in
  `data/gateway-keys.yaml` and are re-read by mtime, so they need no restart.
- The **Android app is a client**, not a service. It connects to the API at
  build-time `WINNIE_PUBLIC_URL` or the address entered on the login screen.
- **Push has no process of its own.** It is a capability inside the API: a
  durable table plus an in-process fan-out. One API worker is enough; more than
  one needs Redis pub/sub, because a publish reaches only the connections on its
  own worker (the backlog still delivers on reconnect). A reverse proxy must pass
  the WebSocket upgrade for `/api/v1/push/ws` (and
  `/api/v1/meetings/{id}/stream`).
- **Phone data has no process of its own.** The Android client uploads batches,
  the API stores them in the application database, and per-kind retention runs
  during each sync; there is no phone worker to supervise. Deleting a device's
  copies is an action on the phone screen or
  `DELETE /api/v1/phone/devices/{id}/data`; revoking the device needs
  `DELETE /api/v1/phone/devices/{id}` (see
  [phone.md](./phone.md#8-privacy-and-security)).
- **Mail has no process of its own.** It is a capability inside the API: the
  owner's SMTP/IMAP/POP3 account is read from `data/mail/<user_id>.yaml` on each
  operation, so a settings change needs no restart. There is no polling loop —
  a caller asks for messages (see [mail.md](./mail.md)).
- The index daemon is loopback-only by design; the browser reaches it through
  the API, and `/admin/*` is not exposed to remote clients.
- In production the admin console is built once (`make admin-build`) and served
  as static files by the reverse proxy; the Vite server on 5173 is development
  only.

## Local lifecycle

The full local stack is the API, the index daemon and the admin console:

```bash
make start      # start API, index and admin in the background
make status     # service, port, state and PID
make stop       # stop them in reverse order
make restart    # stop, then start
```

`make start` detaches each process, writes logs under `.cache/logs/` and PID
files under `.cache/run/`, and waits for a health check before reporting
success. If a port is already owned by the matching service it is left alone;
if it is owned by another program, start refuses and stop refuses to kill it.

Individual services remain available in the foreground for development:

```bash
make api          # API with reload, logs in the terminal
make index-serve  # index daemon, logs in the terminal
make admin        # Vite dev server, logs in the terminal
```

Useful overrides:

```bash
make start API_PORT=8010 ADMIN_PORT=5174 INDEX_PORT=8791
make start SERVICES="api index"          # skip the console
make stop  SERVICES="api"                # stop one service
make status
```

`SERVICES` defaults to `api index admin` in start order; stop walks it in
reverse. The API and index are independent, but the admin proxy and the API's
index client assume the default ports. If you move the index, update
`index.base_url` in `apps/api/config.yaml` as well as `INDEX_PORT`.

Logs and PID files:

```text
.cache/logs/api.log
.cache/logs/index.log
.cache/logs/admin.log
.cache/run/<service>.pid
```

`api.log` carries two kinds of line: uvicorn's access log (`INFO: <client> -
"POST /api/v1/phone/…/sync" 200 OK`) and the application's own records
(`2026-11-01 20:18:57,259 INFO winnie_phone.service phone sync user=… device=…
kind=location mode=incremental accepted=1`). The second kind is what says *what*
a request did — which phone kind it carried and how many rows it touched — and
it also holds every other capability's operational log, which was silently
dropped before `winnie_api.main._configure_logging` installed a root handler.
`WINNIE_LOG_LEVEL=DEBUG make api` lowers the threshold for a session; `httpx`,
`httpcore`, `sqlalchemy.engine` and `watchfiles` stay at `WARNING` so their
chatter does not bury those lines.

State and credentials on disk (all outside git except the database schema):

| Path | Mode | Holds | Notes |
|---|---|---|---|
| `data/winnie.db` | `0644` | Application database | SQLite default; PostgreSQL in production |
| `data/gateway-keys.yaml` | `0600` | Gateway key digests | Re-read by mtime |
| `data/mail/<user_id>.yaml` | `0600` | Mailbox settings **including the password** | Read per operation; `data/mail/` is gitignored |
| `apps/api/.env` | — | Deployment secrets (`AMAP_API_KEY`, the SMTP fallback, …) | Sourced by `make api`; a change needs a restart |

The mailbox file is the reason `data/mail/` must stay in `.gitignore`: unlike the
gateway keys it holds a real secret that can log in to the owner's mail.

`make start` needs:

- `uv` (or `apps/api/.venv`) for the API;
- `apps/admin/node_modules` and `npm` for the console;
- `~/.config/winnie-index/config.yaml` and the API venv for the index — run
  `make index-init` if the config is missing.

## Background jobs

The checked-in configuration uses `jobs.backend: inline`, so the API processes
meetings in the request and **no worker or Redis is needed**. With
`jobs.backend: arq`, start a worker separately (it has no port):

```bash
cd apps/api
uv run arq winnie_api.jobs.worker.WorkerSettings
```

`make start` deliberately does not include the worker: it needs Redis and a
configuration that actually uses arq, and starting it against an inline setup
would add a second consumer without a queue to consume. Use a supervisor or a
systemd unit for it in production.

## Production shape

A single host with TLS typically runs:

```text
:443 reverse proxy (TLS, static admin, /api and /v1 to the API)
  -> 127.0.0.1:8000 API
  -> 127.0.0.1:8790 index (loopback only)
  -> arq worker (no port) -> 127.0.0.1:6379 Redis
  -> 127.0.0.1:5432 PostgreSQL
```

The API binds `[::]` locally so IPv6 clients and the Android app can reach it;
the reverse proxy is the only public listener. See
[README](../README.md) for the current security caveat: the development path is
still cleartext HTTP/WS, and the Android network-security config must be
narrowed after TLS is in place.

## Diagnostics: the local microphone

A voice turn that fails is usually one of two questions: did the room reach the
microphone, and did the server turn it into text? The first one is measurable
without the app:

```bash
make audio-check                                  # speak when prompted
make audio-check ARGS="--play clip.wav"           # speaker -> room -> mic
make audio-check ARGS="--json --quiet 2 --speech 2"
```

`scripts/check-audio-input.py` records a quiet phase and a spoken phase from the
default capture source (`parec`, 16 kHz mono), then reports RMS, peak, DC offset,
clipping, 20 ms frame levels, the 300-3000 Hz band level and the share of power
per band, and writes both phases to `.cache/audio-check/` so they can be played
back. The pass/fail decision is taken in the speech band, because broadband RMS
cannot tell a voice from a hot input's own rumble.

Two limits worth knowing before reading its output:

- The browser's `echoCancellation`, `noiseSuppression` and `autoGainControl`
  change the numbers a lot — the console can read -80 dBFS where this script
  reads -20 dBFS. This is the hardware baseline; the app on top of it is a
  separate measurement.
- A capture chain at high gain (mic boost plus capture gain) makes an open input
  look loud, so a loud broadband number with an empty speech band means "nothing
  audible is connected", not "the microphone is sensitive". `alsamixer -c <card>`
  shows the boost; the script prints the card number.
