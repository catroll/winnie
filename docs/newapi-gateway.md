# Gateway on new-api (deferred, low priority)

**Status:** planned, not started. Deliberately below the current phases: the
in-process gateway works and this migration trades transparency for aggregation.
Do not start it before phase 2 (knowledge base) is delivered.
**Decided:** 2026-11-03. Owner's choices are fixed: data plane **B** (new-api
public), **SQLite**, channel page **common fields only**, accounts and API keys
managed in Winnie, billing not required.

This document is the execution plan: what to build, in which order, with what
acceptance criteria, and what will get worse. `docs/openai-proxy.md` describes
the gateway that runs today; this page describes the replacement.

## 1. What changes

Today the gateway lives inside `apps/api` (`packages/winnie-gateway`): it
authenticates a Winnie key, forwards bytes to a supplier route
(`/deepseek-openai/...`), injects the supplier credential, and resolves model
names through the registry's virtual models. Winnie's console is the only
management surface.

After the migration, **new-api is the data plane**:

- Clients call new-api's OpenAI/Anthropic/Gemini-compatible paths (`/v1/...`,
  `/v1beta/...`) with a new-api token (`sk-...`).
- new-api selects a channel by model name, may translate between protocols, and
  handles retries, channel testing and per-token limits.
- Winnie keeps the accounts, the console and the client-visible API keys. It
  manages new-api through its admin API with a scoped personal access token.
- Winnie's own gateway stops being the data plane. It stays available during a
  parallel run and is then switched off.

**Not in scope:** billing, quota display, user sync, new-api's dashboard as a
user surface, per-key model overrides (no equivalent), arbitrary non-model
endpoint pass-through (see §4).

## 2. Topology

```
client ──▶ reverse proxy :443
             ├─ /v1/, /v1beta/        ──▶ new-api        127.0.0.1:3000
             ├─ /api/v1/              ──▶ Winnie API    127.0.0.1:8000
             ├─ /healthz, /readyz     ──▶ Winnie API
             └─ everything else        ──▶ 404 (new-api's dashboard, /api/*)

Winnie console :5173 ──▶ Winnie API :8000 ──▶ new-api admin API (loopback)
Winnie API     ──▶ data/winnie.db            (accounts, sessions, meetings)
Winnie API     ──▶ data/gateway-keys.yaml    (Winnie key records, token ids)
new-api        ──▶ data/newapi/new-api.db    (channels, tokens, logs)
```

Routing rules that must not be softened:

- new-api's dashboard `/` and admin API `/api/*` are **never public**. The only
  public paths are `/v1/`, `/v1beta/`, Winnie's `/api/v1/` and the health
  probes.
- new-api binds loopback. Its default `main.go` listens on `:PORT` (all
  interfaces), so this needs a one-line fork patch (`127.0.0.1:` + port) or a
  firewall rule; prefer the patch, and record it.
- In development, new-api stays on `127.0.0.1:3000` and Winnie on
  `127.0.0.1:8000`; only the console (5173) talks to Winnie.

## 3. State observed on 2026-11-03

A new-api instance is already running from `/home/catroll/Projects/Forked/new-api`
(`main`, PID 632085, listening on `*:3000`). Its SQLite file `one-api.db` holds
2 users (`admin` = root, `betty` = common), **0 channels**, 1 token. It is a test
instance: treat its data as disposable, but reset it explicitly rather than
assuming.

Facts that shape the plan:

- `PORT` is the only bind control; `ListenAndServe` uses `":"+PORT` (all
  interfaces).
- The admin API requires an **admin session or a scoped personal access token**
  (`Authorization: Bearer <pat>`), and a PAT is created from a **browser
  session with a step-up verification** — bootstrap is manual once, then
  Winnie runs unattended.
- Channel writes are split by permission: `channel:read`, `channel:operate`,
  `channel:write`, `channel:sensitive_write` (delete, change a key),
  `channel:secret_view` (read a key back).
- `POST /api/token/` returns only `{"success": true}` — no id, no secret. The id
  must be found by listing and the secret read separately
  (`POST /api/token/{id}/key`).
- Channel updates are a partial update that skips zero values
  (`DB.Model(...).Updates(channel)`), and channel reads omit `key`. A channel
  form therefore leaves the key empty to keep it and sends a value only to
  replace it.
- Supported relay paths are a fixed list: chat/completions, completions,
  responses, messages, embeddings, rerank, audio, images, moderations, models,
  Gemini `v1beta`. `/v1/files` and fine-tunes answer "not implemented".

## 4. Feature parity and what gets worse

| Capability | Today (Winnie) | On new-api | Disposition |
|---|---|---|---|
| Public path shape | `/{route}/...`, any path forwarded | fixed `/v1`, `/v1beta` set | **Accepted regression.** Audit clients first; endpoints new-api does not implement will 404. |
| Auth | Winnie key (owner, label, allowlist, revoke) | new-api token (`sk-...`) | Winnie key becomes the record; the token is its credential. Revoke = delete token. |
| Key ownership | `owner_id` in `data/gateway-keys.yaml` | no equivalent | Ownership stays in Winnie's key record; new-api sees one service user. |
| Key route allowlist | per-key route allowlist | token `model_limits` | Map to the models of the allowed channels, or drop in v1 (decide at phase 1). |
| Supplier credential | resolved per route from `winnie-llm/.env` | channel `key`, encrypted with `CRYPTO_SECRET` | Winnie writes it once through the channel form; it is never displayed again. |
| Virtual models + aliases | registry catalog, `400` with the published list | channel `models` + `model_mapping` | Re-express each virtual model as its names in the target channel's model list and a mapping to the upstream id. Recommended; phase 1 can also retire them. |
| Per-key overrides | `overrides` map in the key | **no equivalent** | **Dropped.** A group-based approximation is not per-model; do not pretend otherwise. |
| Unknown model | `400` naming published models | `503` "no available channel" | Accepted; can be improved later with a model list in the console only. |
| `/v1/models` | optional pass-through | new-api's own listing | Different shape; clients that parse it must be checked. |
| ask/answer logs, usage ledger | per route, per key | new-api's own logs/DB | **Winnie loses gateway observability.** Phase 6 (optional) reads new-api's `/api/log` for a per-token view. |
| Streaming | SSE bytes passed through | new-api relays and may translate | Accepted; verify one stream per protocol. |
| Header/query pass-through | everything not in a deny list | only modelled fields (+ channel header override) | Accepted; vendor-specific headers need a channel `header_override`. |
| Protocol translation | none | Chat ↔ Responses ↔ Claude ↔ Gemini | New capability; the reason to adopt new-api. |

## 5. What Winnie gains

- A new `newapi` client and a bridge API, so the console owns channels, models
  and keys in one place.
- The current channel/routing limits go away: per-channel retries, multiple
  keys per channel, priorities, weights, model mappings and protocol conversion
  come from new-api.
- The console's Gateway routes page is replaced by a **Model gateway** page with
  channels and models; the API keys page keeps its UX and issues new-api tokens
  behind the scenes.

## 6. Winnie components to build

### 6.1 Configuration

`apps/api/config.yaml`:

```yaml
newapi:
  enabled: true
  # Loopback only. The console talks to this; clients never do.
  base_url: http://127.0.0.1:3000
  # 0600 file holding the personal access token; never in YAML.
  token_file: ../../data/newapi/personal-access-token
  timeout_seconds: 10
```

A missing token file with `enabled: true` is a startup error naming the path. A
process-environment variable `WINNIE_NEWAPI_TOKEN` overrides the file for tests.

### 6.2 `apps/api/src/winnie_api/newapi/`

A typed HTTP client, no ORM and no caching of secrets:

- `client.py`: base URL, PAT header, timeout, retry on connection errors only,
  error mapping (`new-api` answers `{"success": false, "message": ...}` with
  HTTP 200 in several endpoints — map `success` and `message` before status),
  pagination helper.
- `channels.py`: list/get/create/update/delete, enable/disable, `models_enabled`,
  `fetch_models`, `default_base_urls`.
- `tokens.py`: create (then find by name), read key by id, delete, list.
- `status.py`: `/api/status` for the readiness card.
- Tests against `httpx.MockTransport`; every endpoint has a fixture with the
  real envelope shape.

### 6.3 Bridge API (admin-only, session auth)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/gateway/channels` | Paginated channel list (common fields + status) |
| `POST` | `/api/v1/gateway/channels` | Create |
| `PUT` | `/api/v1/gateway/channels/{id}` | Update; omitted `key` keeps the stored one |
| `DELETE` | `/api/v1/gateway/channels/{id}` | Delete (needs the sensitive scope) |
| `POST` | `/api/v1/gateway/channels/{id}/test` | One test request; returns latency and error |
| `POST` | `/api/v1/gateway/channels/{id}/models/fetch` | Pull upstream models into the form |
| `GET` | `/api/v1/gateway/models` | Union of channel models, grouped by channel |
| `GET` | `/api/v1/gateway/status` | new-api version/health for the dashboard |
| `GET/POST/DELETE` | `/api/v1/gateway/keys...` | Existing API keys, now backed by tokens |

All of them return Winnie's error envelope; a new-api outage is a `503` naming
the upstream, not a stack trace.

### 6.4 Key ↔ token bridge

- `GatewayKey` gains `newapi_token_id: int | None` (optional field; files load
  unchanged). A key without one is a **legacy** key: it works only on the old
  data plane and must be reissued to work after cutover.
- Create: `POST /api/token/` with
  `{name: "winnie-<key id>-<slug>", unlimited_quota: true, expired_time: -1,
    remain_quota: 0, group: "default", model_limits_enabled: false}`; find the
  id by listing (the create response has none); `POST /api/token/{id}/key` to
  read the `sk-...` secret; store the id in the key record; return the secret to
  the console once.
- Revoke: `DELETE /api/token/{id}`, then mark the Winnie key revoked. A missing
  token (already deleted) is not an error.
- Reconcile: on the keys page load, compare token ids with new-api's list and
  mark `orphaned` keys and unmanaged tokens with a one-click cleanup. Never
  delete a token automatically.
- Failure modes: new-api down → `503`, nothing written; token created but key
  read failed → delete the token, return an error; duplicate names → match on
  id, not name.

### 6.5 Console

- **Administration → Model gateway**: channel table (name, type, base URL,
  models, group, priority, status, last test) and a create/edit form with the
  common fields:
  `name`, `type` (OpenAI 1, Anthropic 14, DeepSeek 43, Gemini 24, Ali 17),
  `base_url`, `key` (write-only, "leave empty to keep"), `models` (multiline),
  `group`, `priority`, `weight`, `enabled`.
  Plus **Fetch models** and **Test**. No multi-key, parameter/header overrides,
  proxy, tags, model mapping or balance in this page.
- **Workspace → API keys**: unchanged UX. Creation now issues a new-api token
  and shows the `sk-...` secret once; the row shows the token id and whether it
  is `orphaned`.
- **Gateway routes** page is removed at cutover; until then it stays as the old
  data plane's view.
- A banner on the Model gateway page when new-api is unreachable or unconfigured.

### 6.6 Retiring the old data plane

Add `gateway.enabled: true|false` to `apps/api/config.yaml` and register the
gateway capability only when true. During the parallel run both namespaces
exist (`/deepseek-openai/...` on Winnie, `/v1/...` on new-api). After the
cutover, flip the flag; keep the code and the key file for one release as
rollback.

## 7. new-api setup runbook (manual once)

1. **Bind loopback.** Patch the fork's `main.go` to read `HOST` (default
   `127.0.0.1`) and use `HOST+":"+port`; rebuild. Record the patch in
   `docs/decisions.md` (it is the only fork change this plan needs).
2. **Runtime directory.** `data/newapi/` (gitignored): `new-api.db`,
   `crypto.secret`, `session.secret`, `personal-access-token`, all `0600`.
3. **Environment** for the start script / unit:
   `SQLITE_PATH=<abs>/data/newapi/new-api.db`,
   `CRYPTO_SECRET=<from crypto.secret>`,
   `SESSION_SECRET=<from session.secret>`,
   `PORT=3000`, `GIN_MODE=release`, `MEMORY_CACHE_ENABLED=true`,
   `BATCH_UPDATE_ENABLED=false`, `SYNC_FREQUENCY=60`.
4. **Initialize** `POST /api/setup`
   `{username, password, confirmPassword, SelfUseModeEnabled: true,
     DemoSiteEnabled: false}`. Root password goes into the operator's password
   manager, never into the checkout.
5. **Create the PAT.** Log in on `http://127.0.0.1:3000`, create a personal
   access token with the scopes the bridge needs (`channel:read`,
   `channel:operate`, `channel:write`, `channel:sensitive_write`,
   `channel:secret_view`; use the scope catalog endpoint to confirm names), and
   save it to `data/newapi/personal-access-token` (`0600`). The PAT is
   equivalent to a channel administrator; treat it as a secret.
6. **Options**: `SelfUseModeEnabled=true`, `LogConsumeEnabled=false`, demo site
   off, registration irrelevant (the dashboard is not public). Give the service
   user a large quota (e.g. `2^60`) so the wallet check can never block.
7. **Backup**: a daily `sqlite3 new-api.db ".backup '<target>'"`, never a copy
   of a live WAL file; keep 7 days. Include `crypto.secret` in the backup — without
   it the channel keys are unreadable.

## 8. Billing posture (no module)

There is no "no billing" switch. `SelfUseModeEnabled` only lets unpriced models
be listed; `PostConsumeQuota` still decreases the user's and the token's quota
on every request. The posture is therefore:

- tokens `unlimited_quota: true`, `expired_time: -1`;
- service user quota large enough never to bind;
- `LogConsumeEnabled=false` to stop the consumption log rows;
- no quota/balance UI in Winnie, and new-api's own dashboards are not exposed.

If the per-request quota writes ever matter (measure `sqlite3` write time and
WAL growth first), the optional fork patch is a single early return in
`service.PreConsumeTokenQuota` and `service.PostConsumeQuota` behind
`BILLING_DISABLED=true`. That is a code change to an AGPL fork; record it with
the other fork patch.

## 9. Migration and parallel run

1. **Audit** what clients actually call: paths, models, whether they rely on
   arbitrary endpoints, custom headers, per-key overrides. This decides whether
   `/v1` coverage is enough.
2. **Create channels** for the suppliers the registry uses: one channel per
   supplier endpoint, type 1 or 14 by wire protocol, `base_url` from the existing
   route, `models` = the registry's model catalog (or the virtual models, see
   §4), key from `packages/winnie-llm/.env`.
3. **Re-express virtual models** as channel `models` + `model_mapping`:
   `deepseek-flash` and its aliases in the channel's model list, mapping each
   published name to the supplier's id. This is the one parity feature that must
   not be silently lost; if it is deferred, say so in the console and in
   `docs/ai-providers.md`.
4. **Issue tokens.** Existing Winnie keys cannot be migrated: the file stores
   only digests, and the plaintext was shown once. For every active key: create
   a token, store its id on the old key record, hand the `sk-...` to its owner.
   Old Winnie keys keep working on the old data plane meanwhile.
5. **Parallel run.** Public `/v1` is served by new-api from day one; Winnie's
   `/{route}/` paths stay. Each client moves when it receives its token. Watch
   new-api's logs for 401/503/404 and the old gateway's logs for traffic that
   has not moved.
6. **Cutover.** When no client uses `/{route}`: set `gateway.enabled: false`,
   remove the Gateway routes page, update `docs/openai-proxy.md` to point at
   new-api, and keep the old key file for one release.
7. **Rollback** until then: re-enable the gateway flag, keep the old keys valid,
   keep the old route table. After the flag is off, rollback means re-enabling
   it and telling clients to use their old Winnie keys — so do not revoke them
   until the parallel window closes.

## 10. Phases, order, estimates

| Phase | Deliverable | Exit criteria | Estimate |
|---|---|---|---|
| 0 | Audit + new-api bootstrap (loopback patch, setup, PAT, options, backup) | `curl /api/status` from Winnie's host; PAT can list channels; no public `/api` | 0.5 d |
| 1 | `newapi` client + channel bridge + tests | Fake new-api covers create/update/delete/list/test/fetch; no secrets in logs | 2 d |
| 2 | Key ↔ token bridge + keys page wiring | Create shows `sk-` once; revoke deletes the token; orphan reconciliation; failure paths tested | 1 d |
| 3 | Model gateway console page (common fields) | Create/edit a real channel, fetch models, test it, in light/dark/mobile; key never displayed | 2–3 d |
| 4 | Channel/model migration + virtual-model mapping | A real chat call through `/v1` with a new token for each protocol; models list matches the registry | 1 d |
| 5 | Parallel run + cutover + docs | No old-path traffic for 7 days; gateway flag off; docs updated | 0.5 d |
| 6 | Optional: per-key usage from new-api logs; per-key model limits | A token's usage is visible in Winnie | 1–2 d |

Total: roughly 6–8 days of focused work, deliberately after the current phases.

## 11. Testing

- Unit: client envelopes (HTTP 200 with `success: false`), pagination, token
  id lookup, key read, update-without-key, channel test errors.
- Bridge: create → list → revoke; new-api down; token created but unreadable;
  duplicate names; reconciliation.
- Integration script `scripts/newapi-smoke.sh`: setup check, channel CRUD, token
  CRUD, `/v1/models`, one chat call per protocol, one stream, then cleanup.
- Console: `npm run build`; browser check of the channel form and the keys page
  in light/dark and at 390 px, with a real new-api behind it.
- Cutover rehearsal: one client moves to `/v1`, its old key is revoked only
  after a day of clean calls.

## 12. Risks and open questions

| Risk | Impact | Mitigation |
|---|---|---|
| new-api admin API is not a stable contract | console breaks on upgrade | pin the fork commit; contract tests against a fake; upgrade as a deliberate task |
| Arbitrary endpoints stopped working | some client 404s after cutover | phase 0 audit; keep the old gateway during the parallel run |
| Virtual models / per-key overrides lost | platform naming promise broken | map virtual models to channel model mapping; drop overrides explicitly |
| Two databases and two backups | data loss on one side | separate runtime dir; backup both; `crypto.secret` in the backup |
| PAT is channel-admin equivalent | one-file compromise | `0600`, loopback only, rotate on suspicion, no PAT in git |
| Billing still writes per request | SQLite contention at volume | measure; optional fork early-return |
| new-api runs on all interfaces by default | dashboard exposed on the LAN | loopback patch; verify with `ss -ltn` |
| AGPL distribution | license obligations for the new-api binary | separate process; ship source/notices if distributed; Winnie stays Apache-2.0 |

Open questions to settle before phase 1:

1. Keep virtual models as channel model mappings, or retire them? (Recommend
   keep, at least the ids and aliases used today.)
2. Keep per-key overrides? (Recommend drop; no equivalent.)
3. Does any client need an endpoint outside new-api's fixed path list?
4. Is the test instance's data (admin/betty, one token) kept or reset?

## 13. References

- Current gateway: [openai-proxy.md](./openai-proxy.md), [ai-providers.md](./ai-providers.md)
- new-api checkout: `/home/catroll/Projects/Forked/new-api`
- Admin API routers: `router/api-router.go`, `router/channel-router.go`
- Relay routing: `router/relay-router.go`, `middleware/distributor.go`
- Quota path: `service/quota.go`
- Channel/token models: `model/channel.go`, `model/token.go`
