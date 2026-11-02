# Self-repair: the loop, the harness, and what Winnie should own

Status: design, phase P0 not started. Last updated: 2026-11-03.

"Let Winnie repair itself" is three different capabilities with three different
risk profiles. Most of the value is in the first two, and the first one needs no
model at all. This document separates them, records what already exists on this
machine (verified, not recalled), and defines a narrow first slice for the third.

## 1. Three levels, not one

| Level | What it does | Model | Risk | Value | Winnie today |
|---|---|---|---|---|---|
| **L0 recover** | restart a dead service, back off, roll back a bad deploy, tell the owner | no | low | ends the 3 a.m. outage | `scripts/services.sh` start/stop/status, `/healthz`; nothing watches either |
| **L1 diagnose** | read logs, code and test output; name the failing component and the next command | yes | medium (reads only) | shortens triage from an hour to a minute | none |
| **L2 repair** | edit the repository in isolation, run the real checks, return a verified diff | yes | high (writes code) | removes the owner from small fixes | none |

L2 without L0/L1 is a model improvising on a live checkout. L0 without L2 is
ordinary supervision. The order below follows that: make failures visible and
recoverable, then explain them, then fix them.

## 2. What already exists on this machine (verified 2026-11-03)

| Fact | Evidence |
|---|---|
| `dsh` 0.2.0-rc.2 is installed globally and runs | `dsh --version` → `0.2.0-rc.2`; `dsh --profile headless --help` exits 0 |
| The `headless` profile runs one task and prints the final answer | probe below: 8 NDJSON events, `final.text = "ready"`, exit 0 |
| `--json` gives a parseable event stream | events seen: `session`, `status`, `thinking`, `text`, `final`; docs also list `tool_call`, `tool_result` |
| The worker's tools work in the directory it is started in | probe wrote and read `.tmp/devloop-probe/probe.txt` by absolute path under the invoking cwd |
| A failing command is visible to the worker | probe ran `python3 -c 'import sys; sys.exit(3)'`; `tool_result` carried `[exit code: 3]`, the model reported 3 |
| Sessions can be resumed | `--session-id <id>` adopts a persisted session; an unknown id fails before the task runs |
| Two other embedding surfaces exist | `dsh --profile sdk` (JSON-RPC stdio) and `dsh --profile acp` (ACP v1 stdio: sessions, model selection, MCP servers, cancel) |
| A Python ACP client exists | `agent-client-protocol` 0.12.1 on PyPI (pydantic models, stdio transport) |
| The model route can be redirected | the DeepSeek adapter takes `baseURL` (default `https://api.deepseek.com/anthropic`); Winnie's gateway already forwards Anthropic-compatible bytes, so the worker can be billed and logged by Winnie itself. Documented, not yet driven end to end |

Two cautions, both load-bearing:

- **dsh is a release candidate.** Pin the exact version and keep the subprocess
  contract narrow (argv, stdin, stdout NDJSON, exit code). Do not build against
  its internals or its plugin tree.
- **PyPI `deepseek-harness` is not this `dsh`.** It is an unrelated
  third-party client for DeepSeek models (0.3.1, `openai`/`httpx`). Winnie
  launches the installed CLI; nothing depends on that package name.

## 3. What the market actually supplies

| Category | Examples | Gives | Does not give |
|---|---|---|---|
| Coding-agent harness | `dsh` (headless/SDK/ACP), [OpenHands](https://docs.openhands.dev/openhands/usage/agent-canvas/release-notes/v1.27.0.md) 1.27, SWE-agent, Claude Agent SDK, `codex exec`, Aider | the model↔tool loop, file edits, shell, test execution | an incident trigger, isolation, an approval gate, a memory of what it learned |
| Error→fix service | [Sentry Seer](https://sentry.io/changelog/seer-sentrys-ai-debugger-is-generally-available/) (GA), [Copilot Autofix](https://learn.microsoft.com/en-us/azure/devops/repos/security/github-advanced-security-autofix?view=azure-devops) | detection plus a suggested patch for a known error class | a closed SaaS boundary, its own signal only, no local repo or deploy workflow |
| Process supervision | systemd, container probes, supervisord | L0 restart and health | cannot read code |

So there is no drop-in product for "a self-hosted Python repo that notices its
own failures and patches them behind a gate". The harness is the commodity part
and `dsh` supplies it; the trigger, the isolation, the verification and the gate
are the part Winnie has to own — and they are the part that makes the difference
between self-repair and self-harm.

## 4. Decision

**`dsh` runs out of process as the coding worker. Winnie keeps the loop, the
incident record, the worktree, the checks and the gate.**

- Out of process, because the worker must not share a crash domain, a working
  directory or an event loop with the API the owner is talking to, and because a
  pinned subprocess contract survives a harness upgrade.
- Headless first, ACP later: `--json` is enough for one-shot repairs and needs
  ~100 lines of Python; ACP buys sessions, cancellation and progress streaming
  when a repair becomes long enough to need them.
- The worker's model calls go through Winnie's own gateway route, so a repair is
  priced, logged and attributable like every other model call.

**Rejected: embed dsh as a Node library inside the Python host.** Winnie is
Python; there is no supported in-process binding, and even a sidecar library
would put the harness's lifecycle inside the API's. A subprocess is the same
integration with a smaller contract.

**Rejected: let the assistant's `LocalExecutor` repair code inline.** It writes
files and runs commands by design, but it has no reproduction step, no test gate,
no worktree and no diff to review — a confirmation dialog is not a verification.
`LocalExecutor` stays the gate for the owner's *documents*; code changes go
through the loop below.

**Rejected: adopt a SaaS autofix service.** It would not see this repo's tests,
its deploy, or its local services, and it would move the incident data off the
host that owns it.

## 5. Architecture

```
services (api, index, admin) ──logs/health──► Detector ──► incidents table
                                                            │
                                              RepairAgent ◄─┘ (one at a time)
                                                    │
                        git worktree  .worktrees/inc-<id>  on branch fix/<id>
                                                    │
                    dsh --profile headless --json  (cwd = worktree)
                        tools: read/write/edit/bash
                                                    │
                                              Verifier (repo's own commands)
                                                    │
                                        proposed diff + evidence
                                                    │
                              Gate: console / push / reply  ──► owner approves
                                                    │
                                    apply on branch, rebuild, restart, verify
```

| Piece | Lives in | Responsibility |
|---|---|---|
| `Detector` | `apps/api/devloop` | health poll, 5xx rate, failing scheduled job, a manual "report this" button; writes an incident and its evidence bundle |
| `IncidentStore` | SQLite (existing app DB) | symptom, evidence paths, state, budget spent, transcript reference, diff |
| `RepairAgent` | `packages/winnie-devloop` | creates the worktree, renders the prompt, runs `dsh`, parses the NDJSON stream, enforces budget and kill switch |
| `Verifier` | `packages/winnie-devloop` | runs the named check plus the affected package's suite; a repair is only `verified` when the failing check passes and the previously passing set does not regress |
| `Gate` | `apps/api/devloop` + console page | approval is a code path, never a prompt instruction |
| `Reporter` | console page + push | diff, commands, cost, and a draft lesson for `docs/LESSONS-*.md` |

The package imports `winnie-core` only; the host injects paths, routes and the
approval callback, exactly as `winnie-local` does.

## 6. The repair contract

An incident moves through one state machine and cannot skip a state:

`detected → reproduced → patched → verified → proposed → approved | rejected → applied | abandoned`

Five rules make the difference between a useful loop and a confident mess:

1. **Reproduce before editing.** A repair must name the failing check and show
   it failing before the first write. No reproduction, no repair: the incident
   becomes an L1 diagnosis and stops. A bug that cannot be reproduced cannot be
   verified as fixed.
2. **Worktree, always.** The live checkout runs the API; a repair that edits it
   takes the assistant down with it. `.worktrees/inc-<id>` on `fix/<id>`,
   deleted when the incident closes.
3. **The check is the spec.** "Done" means the reproduced failure now passes
   *and* the suite that passed before still passes. The worker's own claim of
   success is not evidence; the exit code is.
4. **Budget, then stop.** Wall clock, model tokens, files touched, diff lines
   and attempts are capped per incident. An exhausted budget closes the
   incident as `abandoned` with its evidence — it does not retry until it
   accidentally succeeds.
5. **Nothing is applied without the owner.** The loop produces a diff and
   evidence; applying it is a separate, approved action that leaves a branch and
   a rebuild. This is the same rule as `LocalExecutor`: *the gate is code, not
   prompt*.

## 7. Safety

- **Untrusted input.** Logs, issue text and web pages can carry instructions
  aimed at the worker. They enter as evidence files, the tool set stays fixed,
  and no state transition depends on the worker's prose.
- **Secrets.** The worker's environment is scrubbed the way `CommandRunner`
  scrubs it; Winnie's gateway key must be route-scoped to the repair route; the
  workspace deny list (`.env`, `data/`, keys, `.git` writes) is reused from
  `winnie-local`.
- **Network.** The worker may reach the model route and package registries;
  tests that need the internet are marked, not silently enabled.
- **Blast radius.** A worktree, a branch, no push, no service restart until an
  approved apply. Repairs never touch `main`, the live services directory or
  production data.
- **Kill switch.** One config flag disables detection; one cancels the running
  repair and kills its process group.
- **Audit.** Every incident keeps its prompt, transcript, tool calls, diff and
  cost. The owner can read why a change was proposed.

## 8. Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 — supervision** (no model) | health watcher, incident table, restart with backoff, owner notification | Killing the API process produces an incident and a recovery; `make status` reports it; no model call |
| **P1 — diagnosis** (read-only) | evidence bundle → a `diagnose` capability that answers with component, file:line and next command; console button "explain this incident" | A seeded bug is explained with the right file and a working next command; the tool set cannot write |
| **P2 — repair, gated** | worktree, headless worker, verifier, diff proposal, approval | A seeded bug (one failing test) is reproduced, patched in the worktree, verified, proposed; applying it turns the suite green; rejection leaves the tree untouched |
| **P3 — autonomy, graduated** | auto-apply for one named, narrow class; nightly sweep; success-rate metric | A named class (e.g. documentation drift, a failing lint rule) closes without the owner; every auto-apply is revertible in one command and the metric is visible |

P0 is worth doing even if P2 never ships. P2 is worth doing only if P1's
evidence bundle exists, because a repair prompt built from a raw log tail wastes
the worker's context and hides the reproduction.

## 9. Non-goals

- Unattended merges to `main`, or any push.
- Repairing the Android client, the firmware or a deployment from another
  machine.
- Repairing production data (rows, mailboxes, knowledge files) — that is an
  operation with a backup and a restore, not a code repair.
- A second model vendor for the worker; one route, one bill.
- A general-purpose "agent that does anything": the worker gets one incident,
  one worktree, one check.

## 10. Open questions for the owner

1. **Which failure classes first?** Failing tests in `make` targets, API 5xx
   rate, a crashed service, or the assistant's own tool errors?
2. **What may auto-apply in P3?** Documentation-only, test-only, dependency
   patch bumps, or nothing at all?
3. **Budget per incident** — e.g. 20 minutes, 200k tokens, 200 diff lines, 3
   attempts.
4. **Where does approval arrive** — console page, push notification, or an email
   reply that the mail cache already ingests?
5. **Which model route** does the worker use, and is it the same route the
   assistant uses?

## 11. Verification log

```sh
dsh --version                        # 0.2.0-rc.2
dsh --profile headless --help        # exit 0; task/--json/--session-id only
```

```sh
cd .tmp/devloop-probe
dsh --profile headless --json "Reply with the single word ready."
# 8 events: session, status, status, thinking, text, status, status, final
# final.text = "ready", exit 0
```

```sh
dsh --profile headless --json "Create probe.txt with the content ok, then read it back."
# tool_call write -> tool_result completed; tool_call read -> tool_result "1: ok"
# file created under the invoking cwd: .tmp/devloop-probe/probe.txt (2 bytes)
```

```sh
dsh --profile headless --json "Run: python3 -c 'import sys; sys.exit(3)'. State the exit code."
# tool_result: "(no output)\n[exit code: 3]"; final: "The exit code observed was 3."
```

The probe directory is throwaway and gitignored.
