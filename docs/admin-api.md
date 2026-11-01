# Admin API and route authorization

The API is the authority on roles. The console may hide navigation for a normal
user, but every operator route checks the caller's role server-side on every
request. A cached role in the browser is UI state only.

## Roles

`users.role` is a short string, currently `admin` or `user`.

| Rule | Behaviour |
|---|---|
| First registration on an empty `users` table | `admin` |
| Later registrations | `user` |
| Existing database upgraded before this change | Oldest **active** user becomes `admin`; everyone else is `user`. If every existing user is inactive, nobody is promoted; set `role = 'admin'` manually once the account is reactivated. |
| Accounts disabled (`auth.enabled: false`) | The configured local user (`meetings.default_user_id`, or `local`) acts as `admin` |
| Last active administrator | Cannot be demoted or deactivated (`409`) |
| Caller's own account | Cannot be deactivated (`400`) |
| Password reset or deactivation | Revokes every session belonging to the target |
| No hard delete | Accounts are deactivated, never removed, in this iteration |

`UserRead`, `POST /api/v1/auth/register`, `POST /api/v1/auth/login` and
`GET /api/v1/auth/me` all return `role`. A role change applies on the caller's
next request because the session lookup reloads the user; it is not cached in
the token.

## User management

Mounted at `/api/v1/admin/users`; every route requires `admin` and returns `403`
for a normal user.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/admin/users` | List/search. Query: `q`, `role`, `is_active`, `limit` (1-200, default 50), `offset`. Returns `items`, `total`, `limit`, `offset`; each item has `id`, `username`, `display_name`, `role`, `is_active`, `created_at`, `updated_at`, `last_session_at`, `active_sessions`. |
| `POST` | `/api/v1/admin/users` | Create `{username, display_name?, role}`. Returns the user plus `temporary_password` once. |
| `PATCH` | `/api/v1/admin/users/{user_id}` | Partial update of `display_name`, `role`, `is_active`. Omitted fields are unchanged; explicit `null` clears `display_name`. |
| `POST` | `/api/v1/admin/users/{user_id}/reset-password` | Generate a password (returned once) and revoke all sessions. |
| `POST` | `/api/v1/admin/users/{user_id}/activate` | Activate the account. |
| `POST` | `/api/v1/admin/users/{user_id}/deactivate` | Deactivate and revoke all sessions. |

Responses never contain `password_hash` or a token. Passwords are generated
server-side, returned exactly once, and only an Argon2 hash is stored.

A normal user also receives `403` for every route below, even when its feature is
disabled: the gate is a router-level dependency and runs before the endpoint can
answer `503`.

## Operator-only routes

| Area | Routes | Gate |
|---|---|---|
| Users | `/api/v1/admin/users*` | `admin_required` per route |
| AI registry | `GET /api/v1/ai/config`; `POST /api/v1/ai/vendors`; `POST /api/v1/ai/vendors/{id}/models/sync`; `PUT /api/v1/ai/vendors/{id}`; `POST/PUT/DELETE /api/v1/ai/integrations*`; `PUT /api/v1/ai/runtime`; `PUT /api/v1/ai/features/{feature}` | router-level `admin_required` |
| Gateway inspection | `GET /routes` | `admin_required` on the route |
| Document index proxy | `GET /api/v1/index/status`; `GET/PUT /api/v1/index/config`; `POST /api/v1/index/{search,build,skill}` | router-level `admin_required` |
| News | every `/api/v1/news/*` route (status, runs, drafts, sources, events) | router-level `admin_required` |
| Global usage | `GET /api/v1/usage/overview`; `GET /api/v1/usage/records`; `GET /api/v1/usage/balances`; `POST /api/v1/usage/balances/refresh` | router-level `admin_required` |

The AI and news routers are only registered when their feature is enabled, and
the index proxy only when `index.enabled` is true. When they are registered, the
gate is always present.

## Owner-scoped routes (any authenticated role)

These remain available to a normal user because they operate on the caller's own
data, not the instance:

- **Account** — `POST /api/v1/auth/logout`, `POST /api/v1/auth/password`,
  `GET /api/v1/auth/me`.
- **Meetings** — `/api/v1/meetings*` and
  `WS /api/v1/meetings/{meeting_id}/stream`; rows are filtered by owner.
- **Assistant / chat / tasks / skills** — `/api/v1/assistant/*`: chat,
  conversations, memory, document search, skills, tasks and task runs, gold,
  weather, and the local `/assistant/ui` page.
- **Phone** — `/api/v1/phone/*`; owner-scoped through `get_user_id`. These files
  were deliberately not modified by the user-management change.
- **Push** — `/api/v1/push/notifications`, `/status`, `/test` and
  `WS /api/v1/push/ws`; a device belongs to one account.

## Public routes

| Route | Why it is public |
|---|---|
| `GET /healthz`, `GET /readyz` | Liveness and readiness probes. |
| `POST /api/v1/auth/register` | Bootstrap. The first account can always register even when `auth.allow_registration` is false; later registrations are refused with `403`. |
| `POST /api/v1/auth/login` | Obtain a session. Rate-limited by `login_throttles`. |
| `/{route}/{path}` | The model gateway's data plane. It authenticates with a Winnie gateway key, not a console session, and injects the route's supplier key from `packages/winnie-llm/.env`; this is not a console surface and gating it on a session would break non-console clients. |

Gateway keys are managed through the console API:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/gateway/keys` | List keys: id, label, allowlist, creation and revocation time. Never the secret or digest. |
| `POST` | `/api/v1/gateway/keys` | Create a key; the response contains `secret` exactly once. An allowlist may only name configured routes. |
| `DELETE` | `/api/v1/gateway/keys/{id}` | Revoke a key. The record stays for attribution; a second call is idempotent. |

## Deliberately not gated

- **Phone routes** stay owner-scoped. Another workstream owns
  `packages/winnie-phone/**`; changing its authorization is out of scope here.
- **Gateway data-plane routes** stay open (see the table above): a caller may
  reach them without a session, because a key is the credential there. Only
  route inspection (`GET /routes`) and key management are admin-only.
- **Meetings, chat, assistant tasks, skills and account** stay available to
  normal users by product decision; they are owner-scoped rather than global.

No operator router was too entangled to gate in this iteration.

## Known limits

- Deactivation and password reset revoke bearer sessions. An **already-open**
  push or meeting WebSocket authenticated only at connect and is not force-closed
  by the user-management endpoints; the same is true of logout today. The
  revoked token cannot be used to open a new connection.
- The last-administrator guard is checked and updated in one transaction, with a
  row lock on the active administrator rows on PostgreSQL. SQLite ignores
  `FOR UPDATE`; its writer lock still serializes the update, but the guard has
  not been stress-tested under concurrent SQLite writers.

## Accounts disabled

With `auth.enabled: false` there is no login, so every request is the trusted
local operator and `admin_required` treats it as `admin`. User-management
endpoints work when a database-backed runtime exists, which lets a local
deployment prepare accounts before turning login on. Without a database runtime
they return `503`; they never silently create a user.
