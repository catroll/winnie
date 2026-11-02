# Self-repair: what shipped, and the plan for repair

Status: **P0 and P1 shipped 2026-11-03**; P2 (manual repair) and P3 (patrol)
planned, not started. Last updated: 2026-11-03.

"Let Winnie repair itself" is three different capabilities with three different
risk profiles. Recovering a dead service needs no model, explaining a failure
needs a model but no write access, and patching the repository needs both plus
isolation and a gate. This document records what is running, and specifies the
two phases that have not started, so a later session can execute them without
re-deciding the design.

## 1. Three levels

| Level | What it does | Model | Risk | Status |
|---|---|---|---|---|
| **L0 recover** | detect a dead or unhealthy service, restart it with backoff, record it | no | low | **Shipped** — `scripts/watchdog.py` + the in-process detector |
| **L1 diagnose** | read the incident, logs, git state and health; name the component, file and next read-only step | yes | medium (read-only) | **Shipped** — console **Diagnose** |
| **L2 repair** | reproduce in isolation, patch, run the repo's own checks, propose a verified diff | yes | high (writes code) | **Planned** — P2 (manual) and P3 (patrol) |

L2 without L0/L1 is a model improvising on a live checkout. That is why the
first two shipped first, and why they are the foundation of the third: the repair
phase consumes exactly the evidence bundle L1 produces.

## 2. What shipped

### 2.1 Collection

Every failure is normalised into one incident and deduplicated by fingerprint.

| Source | Caught by | Example |
|---|---|---|
| `api` | ASGI middleware (`ErrorCaptureMiddleware`) | an unhandled exception, or a 5xx a handler returned |
| `log` | root `logging` handler (`IncidentLogHandler`) | an `ERROR` from any capability, including background tasks |
| `detector` | in-process poller | the index daemon or console not answering |
| `watchdog` | spool drain | a service that died and was restarted |
| `manual` | reserved | a future "report this" button |

The fingerprint is `sha256(kind, service, exception_type, location, masked
message)[:32]`, where the message masks UUIDs, `0x…` identifiers and numbers of
three digits or more. One bug is one row: `occurrences` and `last_seen` move,
`first_seen` and the first traceback stay. `location` is the innermost frame
inside this checkout that is not installed code and not the collection layer
itself — a virtualenv under `apps/api/.venv` would otherwise make every
incident point at fastapi's router.

Kinds: `unhandled_exception`, `http_error`, `logged_error`, `service_unhealthy`,
`service_down`, `service_recovered`. A recovery row is stored `resolved` on
insert: it is history, not work.

Statuses: `open`, `acknowledged`, `ignored`, `resolved`. A resolved incident that
fires again reopens; an ignored one keeps the owner's decision. A service
incident closes itself when the next probe succeeds or a recovery event arrives —
an "it is fine now" row the owner has to clear by hand is a false alarm.

### 2.2 Supervision

`scripts/watchdog.py` polls `/healthz` per service (API, index, admin), restarts
a dead one through `scripts/services.sh restart <name>`, and leaves JSON lines in
`data/devloop/spool/`. Backoff is 5s, 15s, 45s, 120s, 300s, then a
`--cooldown` (default 10 minutes) before trying again. It writes to the spool,
not the API, because the incident that matters most cannot be posted to the API
while the API is down.

The spool drain waits 2 s after a file's last write, then folds it and deletes
the file. Events that arrive while the API is down are therefore kept, and a
file being written is never read half-way.

The in-process detector covers what a process can see: a periodic HTTP probe of
`devloop.service_urls` (index, admin) and the spool drain. It cannot cover the
API's own death — that is the watchdog's job.

### 2.3 Diagnosis (L1)

**Diagnose** on an incident builds a bounded evidence bundle and makes exactly
one model call:

- the incident (message, kind, service, location, traceback, context, counts);
- the repository (`git rev-parse`, `status --short`, `log --oneline -8`,
  `diff --stat`);
- the tail (120 lines) of the incident service's log and of `api.log`;
- a one-shot health probe of the configured services.

The bundle is capped at `devloop.diagnose.max_evidence_bytes` (24 KB), dropping
log tails first and then truncating the traceback. The system prompt forbids
guessing beyond the evidence and forbids write/restart/delete steps in
`next_steps`; the answer is JSON (`component`, `file`, `line`, `cause`,
`confidence`, `next_steps`, `notes`), parsed leniently, with the raw text kept
when parsing fails. The diagnosis is stored on the incident with the model name
and time, because the evidence stops existing when the logs rotate.

The model is the assistant's configured chat model (`assistant.chat` binding),
injected by the host. No binding means `503 diagnosis is unavailable: no chat
model is bound` — never an empty answer.

### 2.4 Where the code lives

| Piece | Path |
|---|---|
| events, fingerprint | `apps/api/src/winnie_api/devloop/events.py` |
| table | `devloop/models.py` + migration `b7c1e4d92a30_error_incidents.py` |
| store, triage, diagnosis | `devloop/service.py` |
| evidence bundle | `devloop/evidence.py` |
| prompt and parser | `devloop/diagnose.py` |
| middleware, log handler | `devloop/capture.py` |
| detector, spool drain | `devloop/detector.py` |
| console API | `devloop/router.py` |
| lifecycle wiring | `devloop/setup.py` + `main.py` |
| supervisor | `scripts/watchdog.py` |
| console page | `apps/admin/src/pages/ErrorsPage.tsx` |

**Why it is a host module, not a capability package.** Collection observes every
capability's loggers and the whole ASGI app, and must keep working when a
capability is switched off — the same argument that keeps the usage ledger in the
host. A package would have to be imported by the host, may not import the host,
and would still need the host to install the middleware. When P2 grows a state
machine, a worktree and its own CLI, that part becomes `packages/winnie-devloop`
with its own tests; the collection seam stays here.

### 2.5 API and configuration

| Route | Purpose |
|---|---|
| `GET /api/v1/errors` | list, filters `status`/`service`/`source`/`query`/`limit`/`offset` |
| `GET /api/v1/errors/summary` | open, total, last 24 h, by status, open by service |
| `GET /api/v1/errors/{id}` | one incident |
| `POST /api/v1/errors/{id}/status` | `open`/`acknowledged`/`ignored`/`resolved` |
| `POST /api/v1/errors/{id}/diagnose` | one read-only model call; stores the result |

All are administrator-only (router-level `admin_required`): a traceback names
internal paths and data shapes. The router is mounted even when collection is
disabled, and answers `503` with the reason rather than a 404.

```yaml
devloop:
  enabled: true
  capture_client_errors: false   # 4xx is usually the caller's mistake
  retention_days: 30             # resolved/ignored are pruned daily
  log_dir: ../../.cache/logs
  spool_dir: ../../data/devloop/spool
  service_urls:                  # what the API cannot see about itself
    index: http://127.0.0.1:8790/healthz
    admin: http://127.0.0.1:5173/
  detector: { enabled: true, interval_seconds: 30, probe_timeout_seconds: 3 }
  diagnose: { enabled: true, timeout_seconds: 45, max_evidence_bytes: 24000 }
```

### 2.6 What it does not capture

- Errors that happen before the app exists (an import failure in `create_app`):
  they reach stderr, not an incident. The watchdog's `service_down` covers the
  outcome, and its evidence carries the log tail.
- Browser-side console errors: the page is a client, not a service.
- The Android client: it reports through its own logs; a server-side detector for
  it would need a heartbeat first.
- 4xx answers unless `capture_client_errors` is turned on.

## 3. The repair contract (P2 and P3)

An incident moves through one state machine and cannot skip a state:

`detected → reproduced → patched → verified → proposed → approved | rejected → applied | abandoned`

Five rules, unchanged from the original design:

1. **Reproduce before editing.** A repair must name the failing check and show
   it failing before the first write. No reproduction, no repair.
2. **Worktree, always.** `.worktrees/inc-<id>` on `fix/<id>`; the live checkout
   runs the API and must not be the thing being edited.
3. **The check is the spec.** "Fixed" means the reproduced failure passes *and*
   the previously passing set still passes. Exit codes, not the model's prose.
4. **Budget, then stop.** Wall clock, tokens, files touched, diff lines and
   attempts are capped per incident; exhaustion ends the incident with its
   evidence.
5. **Nothing is applied without the owner.** The loop produces a diff and
   evidence; applying it is a separate, approved action. The gate is code, not
   prompt — the same rule `LocalExecutor` already follows.

## 4. P2: manual repair, one incident at a time

**Goal.** The owner opens an incident in the console and clicks **Repair**. The
worker reproduces the failure in an isolated worktree and returns a diff with the
commands that were run, their exit codes, and the diff itself. Nothing on the
live tree changes.

### 4.1 Flow

1. `POST /api/v1/errors/{id}/repair` (admin). Refused when: the incident is not
   `open`/`acknowledged`, another repair is running (one at a time), the tree is
   dirty (`git status --porcelain` non-empty), or `devloop.repair.enabled` is
   false.
2. The host creates `.worktrees/inc-<short id>` on a new branch `fix/<short id>`
   from the current `HEAD`, and records an attempt row with the incident id.
3. The worker runs `dsh --profile headless --json` with `cwd` = the worktree and
   a task built from the L1 evidence bundle. The task text states: the failing
   check, the requirement to reproduce it before editing, the files/areas it may
   touch, and that it must leave the change in the worktree and stop.
4. The verifier runs the mapped checks (see 4.3): the reproduction first, then
   the affected suite. The attempt is `verified` only when the reproduction now
   passes and nothing that passed before now fails.
5. `git diff HEAD` in the worktree produces the proposal. It is stored with the
   check output and the model transcript path; the incident becomes `proposed`
   and links the branch.
6. The console shows the diff and the exact commands:

   ```sh
   git merge --ff-only fix/<short id>
   git worktree remove .worktrees/inc-<short id>
   ```

   The owner runs them, or rejects and runs `git worktree remove --force`.
   Winnie never merges: an `--ff-only` merge is the only apply path, and it stays
   a human action until a tested rollback exists (see 4.6).

### 4.2 The worker

`dsh` 0.2.0-rc.2 is pinned; the contract is argv → NDJSON on stdout → exit code.
Events consumed: `session`, `status`, `tool_call`, `tool_result`, `text`,
`final`. The proposal is the worktree diff, never the model's prose.

Before P2 starts, two things must be verified against the harness (they were not
needed for P1):

- the profile used for repairs permits `write`/`edit`/`bash` inside the worktree
  without an interactive approval prompt (a headless run has no human to answer);
- a run can be cancelled and its process group killed when the budget expires.

### 4.3 Verification map

There is no single `make test`. The mapping from an incident's `location` to the
checks that must pass:

| Location starts with | Checks |
|---|---|
| `apps/api/` | `cd apps/api && .venv/bin/python -m pytest -q`, plus `tests/test_migrations.py` if `models.py`/`migrations/` changed |
| `packages/<name>/` | that package's test suite; `apps/api` suite if the change crosses its API |
| `apps/admin/` | `npm run build` (typecheck + i18n + bundle) |
| `docs/`, `README.md` | none — a docs change is verified by review |
| unknown | `make status` + the affected service's health check |

A repair that touches application code but cannot name a failing check is not a
repair: it becomes `abandoned` with the diagnosis attached.

### 4.4 Budget

| Cap | Default |
|---|---|
| wall clock per attempt | 20 min |
| model calls per attempt | 1 headless run |
| diff | 300 lines, 10 files |
| attempts per incident | 1 (P2); 3 (P3, with a fresh worktree each time) |

### 4.5 Storage

An `error_repair_attempts` table (new migration): incident id, branch, worktree
path, state, started/finished, exit code, checks (JSON), diff, transcript path,
failure reason. The incident keeps a pointer to the latest attempt. Deleting an
incident does not delete its worktree; `make repairs-prune` removes worktrees
whose incident is closed.

### 4.6 Deliberately not in P2

- Auto-apply, or applying from the console. The merge stays manual until there is
  a tested rollback: a `--ff-only` merge on a dirty tree is how a repair becomes
  an outage.
- Repairing the Android client or a deployment on another host.
- Network access beyond the model route.

## 5. P3: patrol, scheduled repair

Prerequisite: P2 has produced at least a handful of proposals a human accepted,
so the failure modes and the false-positive rate are known.

### 5.1 Patrol

A nightly run (systemd timer or the assistant scheduler) that executes the
repository's own checks without a model:

| Check | Command |
|---|---|
| API suite | `cd apps/api && .venv/bin/python -m pytest -q` |
| package suites | `make index-test`, `make schedule-test` and the rest |
| console | `cd apps/admin && npm run build` |
| services | `make status`, plus an HTTP probe of each |
| migrations | `alembic check` (schema drift is a failure, not a warning) |

A failing check becomes an incident with `source: patrol` and the command output
as evidence. Patrol itself never repairs.

### 5.2 Graduated autonomy

Only a named class of incident may repair itself, and only after a measured
track record:

| Class | Examples | Gate |
|---|---|---|
| docs | a link that no longer resolves, a stale table | test-free; review the diff in the digest |
| tests | a flaky assertion, a fixture that drifted | the suite it belongs to must pass |
| dependency patch bump | a `patch` update with no API change | full affected suite + build |

Everything else stays manual. Each auto-apply is revertible with one command
(`git revert <sha>`) and leaves a digest entry naming the incident, the diff and
the checks.

### 5.3 Metrics that decide whether P3 continues

- proposals accepted vs rejected, by class;
- median time from incident to green;
- revert rate (any revert above ~10% means the gate is too loose);
- incidents created by a repair (a repair that breaks something else).

Graduation: a class may auto-apply after ten consecutive accepted proposals with
zero reverts.

### 5.4 Rollback, which P3 requires

Auto-apply needs the opposite of apply. Before P3 ships: apply on a clean tree
only, record the pre-apply `HEAD`, run the checks after the merge, and revert with
`git revert --no-edit <sha>` on failure — never `reset --hard`, which would throw
away the owner's uncommitted work.

## 6. Open questions for the owner

1. **P2 apply.** Reproduce-and-propose only (current design), or add a console
   Apply button once a rollback is tested?
2. **Budget.** 20 minutes / 300 diff lines / 1 attempt — is that the right size?
3. **Which models.** The repair worker needs a stronger model than the
   diagnostician; same gateway route or a separate one?
4. **P3 classes.** Docs-only first, or tests too?
5. **Approval channel.** Console only, or also a push notification when a
   proposal is ready?

## 7. Verification log

Shipped behaviour, verified on 2026-11-03:

```sh
cd apps/api && .venv/bin/python -m pytest tests/test_errors.py -q   # 13 passed
cd apps/api && .venv/bin/python -m pytest -q                        # 586 passed
cd apps/admin && npx tsc --noEmit                                   # exit 0
cd apps/admin && npm run check:i18n                                 # 974 keys
```

Live stack (API on `:8000`, reloaded with `devloop.enabled: true`):

- `watchdog.py --once --services index`: detected the stopped index, restarted
  it, wrote `service_down` then `service_recovered` to the spool;
- the spool drained into `error_incidents` within one detector tick, the down
  incident auto-resolved on recovery, and the spool directory emptied;
- while the index was down, the console's `GET /api/v1/index/status` 503s were
  collected as one `http_error` incident with 4 occurrences, and the detector's
  probe failures as one `service_unhealthy` with 12.

Console (headless Firefox over WebDriver BiDi, a throwaway API on `:8001` with
seeded incidents, auth enabled):

- `/errors` renders the counts, the filter row, the table and the nav entry;
- the detail panel shows the diagnosis (file, cause, 72 % confidence, next
  steps), the traceback and the context;
- **Diagnose** without a bound model surfaces
  `503 diagnosis is unavailable: no chat model is bound`;
- light and dark themes both render.

The demo instance, its database and the probe scripts lived under
`.tmp/errors-demo/` and were removed afterwards; nothing from it is committed.
