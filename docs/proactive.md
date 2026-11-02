# Proactive Winnie: noticing, deciding, reaching the owner

Status: **core implemented and running**, 2026-11-02. Done: the lunar fix,
contact birthdays, the chat lookup, the memory guard, the engine
(`winnie-proactive`) with planning, policy, ntfy/email channels, the ladder, the
digest, the console Inbox, the acknowledgement endpoints, the Android notification
actions and the optional model composer. Channels: push, ntfy and email are
configured. Open: the `winnie` calendar alarm path (§6), a deep link into the
conversation, and a physical-device run. Last updated: 2026-11-02.

Winnie today answers when asked and pushes only what a watch was already told to
watch (weather, gold, scheduled-task results). This document plans the other
half: noticing things on its own, deciding whether they deserve the owner's
attention, saying what to do about them, and making sure the message arrives.

Related: [assistant](./assistant.md), [push](./push.md), [phone](./phone.md),
[state](./state.md), [plan](./plan.md), [decisions](./decisions.md). The
[state](./state.md) proposal covers *what* Winnie can know about the owner's
place, body, focus and availability; this document covers *what it does* with
what it knows.

## 1. The incident that motivates this

On 2026-11-01 the owner asked in chat:

> 圆圆生日是什么时候？

Winnie answered that it had searched the local documents and the phone data and
found nothing, and offered to store the date if told. The answer was wrong on
three independent counts, and the date was already on the owner's phone:

| Row in `phone_calendar_events` | Stored rule | Correct date |
|---|---|---|
| `圆圆两岁生日`, one-off, all-day | none | **2026-12-12** |
| `圆圆生日`, all-day | `FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=12` | **2026-12-20** (lunar 冬月十二) |
| `老婆生日`, timed | `FREQ=YEARLY;BYMONTH=9;BYMONTHDAY=11` | **2026-10-20** (lunar 九月十一; past) |

1. **Nothing looks proactively.** The calendar is only read when a tool is
   called, and the tool's default horizon is seven days
   (`phone_tools.py:574`). The search tool walks 30 days *backwards*
   (`phone_tools.py:546`), so a birthday five weeks ahead is outside both.
2. **The answer was produced without a lookup.** The turn has no tool call. It
   restated `profile/people.md`, where an earlier failed lookup had been
   distilled into a fact — *"the assistant could not find her birthday"*. A
   negative result became knowledge, and knowledge replaced the search.
3. **Had it searched, the date would still have been wrong.** Chinese calendar
   providers store a lunar birthday as a Gregorian start plus an RRULE whose
   `BYMONTH`/`BYMONTHDAY` are the **lunar** month and day. Read by RFC 5545
   rules (`winnie_phone/recurrence.py`, `dateutil.rrule`) the series expands a
   month early. Measured on the real rows:

   | Row | Next `dateutil` occurrence | Lunar truth |
   |---|---|---|
   | `老婆生日` | 2027-09-10 | 2027-10-10 (and 2026-10-20, already past) |
   | `圆圆生日` | 2026-11-12 | 2026-12-20 |
   | `舅舅生日` | 2026-12-21 | 2027-01-28 |
   | `农历新年` | 2027-01-01 | 2027-02-06 |
   | `农历月半（元宵节）` | 2028-01-15 | 2027-02-20 |

   Ten of the eleven yearly rows in the database follow this convention;
   `琪琪的生日` is a plain Gregorian yearly event, which is why the detector has
   to classify rather than assume.

And even a *correct* date has no delivery path today: push has no
acknowledgement, no escalation, no quiet hours and no digest (documented in
[push](./push.md) §10), so "sent" is indistinguishable from "seen".

## 2. What "proactive" means here

An **intervention** is four things, and a feature that has only the first is
not this feature:

1. **a fact** Winnie found by itself, with the row or file it came from;
2. **a decision** that it matters *now* — and if it does not, silence or a
   digest;
3. **one concrete action** the owner can take, or Winnie can take
   directly where §6 grants standing approval;
4. **a delivery that is tracked** until the owner acknowledges it, escalates, or
   it expires.

What it is not:

- not a chatty daily essay nobody reads;
- not an LLM loop over the calendar every fifteen minutes;
- not a second notification for everything Winnie already said in chat;
- not permission to message other people, buy things, or take an irreversible
  action without a confirmation.

The owner has granted **standing approval for reminder actions**: Winnie may
create and change its own calendar reminders and alarms without asking per
item. That approval is scoped in §6 and does not extend to outward messages
or irreversible actions.

### Design rules

| Rule | Why |
|---|---|
| **Detect deterministically, compose with the model.** Dates, windows, dedupe and escalation are SQL and date arithmetic. | The gold watch already proves an alert must not depend on a model being bound; date math is also the thing models get wrong most reliably. |
| **Interrupt only if the owner can act in the next 24 hours.** Everything else is a digest. | The failure mode of proactivity is noise, not blindness. |
| **Every claim cites a row.** | "Winnie says 12-20" must be checkable; a wrong date is worse than no reminder. |
| **One subject, one thread.** All stages about 圆圆's birthday share a subject; the owner can mute the subject. | Four separate alerts about one birthday is the classic annoyance. |
| **Silence is the default; the owner can audit.** Every item is listable with its evidence and its send history. | Trust comes from being able to ask "why did you tell me this?". |
| **A send is not a delivery.** Push proves the socket received it, not that a person read it. | `push.md` §10 already states this gap; the ladder closes it. |
| **Corrections are remembered.** A wrong date is fixed once, in a place both the reminder and the chat read. | Otherwise the owner re-corrects every year. |

## 3. Architecture

```
phone sync ──► phone tables ─┐
contacts ────────────────────┤
weekly schedule (WLB) ───────┼──► detectors: SQL + date math
documents / memory ──────────┤        (calendar, birthdays, work items,
phone SMS / notifications ───┘         signals, promises)
                                            │
                                            ▼
                                    candidates (finding + subject + due time)
                                            │
                                            ▼
                                    policy: priority, quiet hours,
                                    daily budget, dedupe, mute, snooze
                                            │
                                            ▼
                                    composer: template, then optional model
                                            │
                                            ▼
                  channel registry ──► push | console | ntfy | email | calendar alarm
                                            │
                                            ▼
                                    ack ledger ──► escalate | digest | expire
```

Three loops, not one:

- **planner** (every 15 min, or after a sync): scans the horizon and creates
  items at their planned times. Idempotent by unique key.
- **dispatcher** (every 60 s): sends items that are due, after policy.
- **escalator** (every 60 s): re-sends or advances an unacknowledged item's
  channel ladder, and folds what remains into the next digest.

## 4. Sources

| Source | Available today | What it can trigger | Freshness |
|---|---|---|---|
| Phone calendar | **yes** — 140 rows synced | birthdays, anniversaries, appointments, timed meetings, dated deadlines | ≤30 s while the Android app runs |
| Phone contacts | **yes** — 682 rows, **no birthday field yet** | birthdays and anniversaries stored on the contact | daily snapshot |
| Phone SMS | **yes** | appointment confirmations, bills, deliveries, exam/booking notices | seconds–minutes |
| Phone notifications | **yes** — 7 rows so far | what other apps consider urgent; flight/refund/repair notices | seconds |
| Phone location | **yes** — 35 fixes | "you are near the pharmacy", late-night, travel day | 5–15 min |
| Owner state ([state](./state.md)) | **no — proposal only** | whether now is a good moment: asleep, driving, in a meeting | per projection TTL |
| Weekly schedule (WLB) | **yes, not wired to the assistant** | work items by day and time, week goals, unchecked items | on demand |
| Document index / memory | **yes** | promises made in notes or chat, project deadlines, people facts | ≤5 min |
| Weather / gold watchers | **yes** | existing watches | unchanged |

The birthday incident is fixed by the first two rows. The work half of the
request needs the seventh, and the "life admin" half needs the third and fourth.

## 5. The intervention model

`proactive_items` is the unit of work. One row per (subject, occurrence, stage).

| Field | Meaning |
|---|---|
| `id` | UUID |
| `user_id` | Owner |
| `kind` | `birthday`, `anniversary`, `event_soon`, `deadline`, `work_item`, `signal`, `promise` |
| `subject_key` | Stable key: `person:圆圆`, `work:SC-outbound`, `event:<phone id>` |
| `occurrence_key` | The date this stage is about, e.g. `2026-12-20` |
| `stage` | `heads_up`, `prepare`, `day`, `follow_up` (kind-specific) |
| `priority` | 0 interrupt now · 1 notify today · 2 digest · 3 record only |
| `title`, `body` | What the owner sees; body is lock-screen safe |
| `actions` | `[{kind, label, payload}]`: `ack`, `snooze`, `draft_sms`, `add_calendar`, `open_conversation` |
| `evidence` | `[{source, table, id, path, line, excerpt}]` |
| `due_at` | When the underlying thing happens |
| `planned_at` | When this stage becomes sendable |
| `status` | `planned` · `sent` · `acked` · `snoozed` · `muted` · `superseded` · `expired` |
| `channels` | Per-channel `{name, sent_at, receipt, error}` |
| `conversation_id` | The conversation the intervention opened (existing watcher precedent) |
| `ack_token` | Signed token for the email ack link |
| `feedback` | `useful`, `too_early`, `wrong_date`, `not_interested` |

Unique on `(user_id, subject_key, occurrence_key, stage)`, so a re-scan updates
an unsent item instead of duplicating it, and a corrected date marks the old
occurrence `superseded`.

### Corrections and overrides

Dedupe merges sources by normalized subject. `圆圆` has two rows — a one-off
`圆圆两岁生日` on 12-12 and a yearly lunar `圆圆生日` on 12-20 — so the first scan
must ask instead of choosing:

> 发现两个圆圆生日：12-12（一次性事件「圆圆两岁生日」）和 12-20（农历冬月十二，每年重复）。哪个对？

The answer is written to `proactive/subjects.yaml` (owner-editable, like
`tasks/tasks.json`):

```yaml
subjects:
  person:圆圆:
    display_name: 圆圆
    rule: {kind: lunar, month: 11, day: 12}
    note: 阳历 12-12 是一次性事件，按农历过
    confirmed: 2026-11-02
    source: owner
```

Both the reminder path and the chat path read this file, so a correction
answers "圆圆生日是什么时候" too.

## 6. Policy: how Winnie decides not to be annoying

### Priority classes

| Priority | Meaning | Channel | Quiet hours |
|---|---|---|---|
| **P0** interrupt | must act within ~2 h: an event starting soon, a same-day deadline | push (loud) | bypass only for same-day items, and only if `allow_urgent` |
| **P1** notify | today or tomorrow, actionable | push (normal) + inbox | held until `quiet_hours` end |
| **P2** digest | within the horizon, plan now | morning/evening digest + inbox | never |
| **P3** record | informational; the owner may want to look | inbox only | never |

### Budget and dedupe

- `max_interrupts_per_day: 4` — a ledger counts P1 pushes per local day; over
  budget, P1 falls into the digest, while P0 always sends and is only counted
  for visibility.
- One item per `(subject, occurrence, stage)` ever. A stage sent and unacked is
  escalated, never re-planned.
- `quiet_hours: ["22:30", "07:30"]`, configurable per weekday (a Saturday
  morning may differ from a Tuesday). Once the [state](./state.md) projections
  exist, `availability` narrows this further: asleep and driving hold P1 the
  same way, a meeting holds everything but P0, and an `unknown` projection does
  not invent a reason to stay silent.
- `snooze` moves `planned_at`; `mute` silences a subject or a kind for a period;
  both are reversible from the inbox.
- A collapsed catch-up: if the server was down across several stages, send one
  message that names what was missed, not four stale ones.
- A P0/P1 item whose planned moment is older than `catch_up_hours` **expires**
  instead of firing late. A "today is X's birthday" notification at 22:00 after
  the date ended is noise; the digest still carries what is upcoming.

### Standing approval, and where the gate stays

The owner decided (2026-11-01) that Winnie may act without asking for each
reminder. The approval is written down here because "the owner said so in
conversation" is not a boundary a later change can check against.

**Allowed without confirmation:**

- create, update and cancel **its own** calendar reminders and OS alarms, in a
  dedicated `winnie` calendar, capped at `alarms.max_per_day: 2`, for P0/P1
  items only;
- send a notification to the owner on any configured channel;
- ack, snooze, mute and re-plan its own items;
- append a marked item to the weekly schedule (`[winnie]` prefix), since
  `winnie-schedule` only splices the target line and Git makes the change
  visible and revertible.

**Still needs one confirmation, and the reason is not caution but
irreversibility:**

- a message to another person (SMS or any future channel): a wrong recipient or
  a wrong tone cannot be recalled. Winnie drafts it, the owner sends or approves
  it. If the owner later wants this opened too, it is one explicit switch, not a
  side effect of "you may do anything";
- purchases, deletions, and local commands, which keep their existing gates.

The distinction is deliberate: approve the reversible and the in-account
actions generously, and keep one gate on what leaves the account.

### Why not just prompt a scheduled task?

A task is a prompt on a timer. To make one work here it would still need the
horizon, the lunar expansion, the source merge, dedupe, escalation and the
acknowledgement — i.e. everything in this document except the sentence. The
prompt is the composer, not the detector. Scheduled tasks stay the right tool
for "summarize X every morning"; the engine may *suggest* one, never create one.

## 7. Composition: a template, then a model

The model is optional and never decides *whether* to interrupt.

1. **Template (always available).** Name, date, weekday, days left, age when
   known, and a short fixed checklist. No model call, no cost, works with no
   provider configured — the same rule as the gold alert.
2. **Model (when bound).** Rewrites the body for this person and this week:
   pulls the relationship and past context from memory and the index, proposes
   one or two actions, and drafts a message when asked. Output is bounded, must
   not invent a date, and is cached per item so a retry costs nothing.

The model's output is guidance, not fact. Every date, time and name in a sent
message comes from `evidence`; the composer is allowed to be opinionated about
*what to do* and not about *when it is*.

## 8. Channels and the escalation ladder

"Make sure it reaches me" is not one channel. It is a ladder with a proof at
each rung.

| Channel | Reaches | Delivery proof | Survives |
|---|---|---|---|
| Console inbox | any browser with a session | item opened | server up |
| Android push (self-hosted WS) | the phone, seconds | WS cursor (received) | the app process |
| ntfy | the phone or a desktop, seconds | HTTP `200` | the ntfy server |
| Email (SMTP) | any mail client | SMTP `250`; signed ack link | always |
| Phone calendar alarm (standing approval) | the phone's OS | OS alarm; no ack | reboot, app kill |

The owner chose (2026-11-01) to run **all three** of push, ntfy and email: the
app for the rich item, ntfy for a fast bare notification, email as the durable
rung. ntfy can be the public `ntfy.sh` or a self-hosted instance; a self-hosted
one keeps the content on the owner's own infrastructure, which is the reason
the project exists. A generic webhook adapter can be added later so `wecom`
and `bark` are a config choice rather than a rewrite; it is not one of the
three chosen rungs.

Ladder for a P1 item:

```
t+0      push (normal)
t+20m    ntfy   — a few words and a link, for when the app is dead
t+60m    email  — with [已收到] and [稍后提醒] links
t+180m   digest — and it stays in the inbox until ack or expiry
```

At most three escalations after the first send, each configured, each bounded. A
P0 uses the same ladder with shorter deadlines. An ack at any rung stops the
rest.

The last rung is deliberately the digest, not "send more": an item that reaches
the digest is still not lost, and the owner is not chased all day.

### Acknowledgement

- **Android:** a `收到` and a `一小时后` action on any alert whose payload has an
  `item_id`; the receiver posts the ack with the stored session and clears the
  notification. Tapping still opens the app — the deep link into the item's
  conversation is the remaining piece.
- **Console:** opening an item marks it seen; the same buttons are on the item.
- **Email:** the ack link carries a signed token, so no session is needed.
- **ntfy:** carries the same signed link as its `click` action; a tap is a
  receipt, and the ntfy server's `200` is delivery, not reading.

### The honest guarantee

No Android app can promise delivery after a force-stop or in deep Doze; the
foreground service is best effort and [push](./push.md) §10 says so. The
guarantee this design offers is narrower and true: **an item is never silently
dropped — it is retried on a configured ladder, and what remains unacknowledged
appears in the next digest.** The only OS-level guarantee on this hardware is a
calendar event with an alarm; with the owner's standing approval it is a real
rung, not an opt-in.

## 9. A birthday, end to end

Real dates from the table in §1: 圆圆's lunar birthday is 2026-12-20.

| Stage | When | Priority | Where | Example |
|---|---|---|---|---|
| `heads_up` | T-14 | P2 | digest | 圆圆生日还有 14 天（12/20 周日，农历冬月十二）。可以先想礼物，要我记一个「订蛋糕」吗？ |
| `prepare` | T-7 | P1 | push | 圆圆生日还有 7 天。这周定下来：礼物、当天安排、蛋糕。回复「记一下」我就建成日程。 |
| `confirm` | T-2 | P1 | push | 后天就是圆圆生日。礼物定了吗？要我把问候语先拟好，到时你改两个字就能发。 |
| `day` | T-0 09:00 | P1 | push (loud) | 今天 · 圆圆两岁生日。上午发条消息或打个电话都来得及；要我先拟一条吗？ |
| `follow_up` | T+1 | P3 | digest | 昨天是圆圆生日。要记一笔吗（送了什么、有没有通话）？ |

The `follow_up` is what makes the second year better: it records what happened,
and next year's `prepare` can cite it. Without it, proactivity never learns.

Conflict handling (the 12-12 vs 12-20 case): the planner schedules **both**
dates and adds one conflict item that asks which is right. This deviates from
the earlier "refuse until resolved" rule on purpose — refusing would have left
the motivating case (圆圆) silent, and a reminder for a date the owner can
ignore is cheaper than no reminder for the right one. A `prefer` written for
`person:圆圆` in `proactive/subjects.yaml` drops the other source from the next
scan.

### Work half, same pipeline

| Stage | When | Priority | Example |
|---|---|---|---|
| `leave_by` | event start − travel − buffer | P0/P1 | 15:50 带图图去知了上课，路上约 25 分钟，现在该出发了。 |
| `deadline` | T-3 / T-1 | P1 | 周五前给刘笔达发调研结论，今天还有一天。 |
| `overdue` | Monday 09:00 | P2 | 上周有 3 件事没打勾：…（来自周程 checkbox）。 |
| `week_plan` | Monday 08:30 | P2 | 本周 5 个已有安排，周三最满；要不要先排「季度对齐」？ |

The weekly schedule is a Markdown file the owner already edits; this is the
cheapest source of real work intent, and it is currently invisible to the
assistant.

## 10. Where it lives

A new capability package, `winnie-proactive`, on the same boundaries as the
others:

- it imports no sibling capability and not the host;
- it declares small ports — `CalendarPort`, `ContactsPort`, `SchedulePort`,
  `ComposerPort`, `ChannelPort`, `Notifier` — and `apps/api` wires the
  adapters (phone service, assistant model, push, ntfy, SMTP);
- the recurrence and lunar fix stays in `winnie-phone`, where the bug is;
- the assistant gains a `proactive` skill (`proactive_upcoming`,
  `birthdays`, `proactive_ack`, `proactive_snooze`, `proactive_mute`) and the
  console gains an **Inbox** page.

Rejected: fold it into `winnie-assistant`. The detectors are about the owner's
data, not about chat; the dispatch loop must run when no model is bound; and the
assistant package is already the largest one.

Rejected: one watcher per kind, like weather and gold. Two bespoke watchers
were fine; eight would duplicate dedupe, quiet hours, escalation and ack eight
times. Weather and gold can later be expressed as detectors on the same engine.

## 11. First slice: birthdays and the work week

Ordered, each step verifiable on its own. The owner asked for both halves in the
first version, so the schedule source is in the slice, not deferred.

1. **Fix the lunar expansion** (`winnie-phone/recurrence.py`). Classify a rule:
   if `(BYMONTH, BYMONTHDAY)` equals the event's local start date, it is
   Gregorian; if it equals that date's lunar equivalent, it is lunar; expand
   with a lunar calendar; otherwise fall back to RFC 5545 and log. Add the
   eleven real rows as a table-driven test with the expected dates above.
   This also fixes `phone_calendar` for every lunar event, chat included.
2. **Read contact birthdays.** Android `ContactsSource` reads
   `CommonDataKinds.Event` (`TYPE_BIRTHDAY`, `TYPE_ANNIVERSARY`); add
   `birthdays: [{date, type}]` to `ContactItem`, the model, and the snapshot.
3. **Answer the question in chat.** A `birthdays(days_ahead)` tool that scans a
   year, merges calendar + contacts + overrides, and returns the next date with
   its source. Instruction: never answer a birthday from memory.
4. **Stop remembering failed lookups.** The distiller must not store "could not
   find X"; remove the existing `people.md` line about 圆圆; the rule goes in
   the distillation prompt and a test.
5. **The engine skeleton.** Items table + Alembic migration, planner,
   dispatcher, policy, the birthday detector, the console inbox, and push
   delivery through the existing `Notifier`.
6. **Ack and escalation.** `POST /proactive/items/{id}/ack`, the Android action,
   ntfy and email channels, the ladder, quiet hours, the digest.
7. **The work week.** A `SchedulePort` over `winnie-schedule` (the package
   already parses and edits the file), then three detectors: `leave_by` for a
   timed item, `overdue` for unchecked items from earlier days, and `week_plan`
   on Monday morning. Write-back is limited to appending a `[winnie]` line,
   which the editor already does byte-safely.

Steps 1–4 fix the question that started this, steps 5–6 are the first real
intervention, and step 7 is the work half the owner asked for in the same
version. The escalation ladder and the schedule can be built in parallel once
step 5 lands.

## 12. Configuration sketch

```yaml
proactive:
  enabled: true
  timezone: Asia/Shanghai
  horizon_days: 120
  scan_interval_seconds: 900
  tick_seconds: 60
  catch_up_hours: 48
  quiet_hours: ["22:30", "07:30"]
  # A same-day P0 may cross quiet hours; nothing else does.
  allow_urgent: true
  max_interrupts_per_day: 4
  digest:
    morning: "07:30"
    evening: "20:30"
    max_items: 12
  # The host adds "push" when the push capability is enabled; "console" is the
  # inbox and never sends. The ladder walks this order.
  channels:
    order: [push, ntfy, email]
    ntfy:
      enabled: false
      # Self-hosted keeps the content on the owner's infrastructure; ntfy.sh
      # works the same way but is a third party.
      server: "https://ntfy.sh"
      topic: ""
      token_env: WINNIE_NTFY_TOKEN
    email:
      enabled: false
      to: ""
      from_address: ""
      smtp_host: ""
      smtp_port: 465
      username_env: WINNIE_SMTP_USER
      password_env: WINNIE_SMTP_PASSWORD
  # One entry per escalation after the first send, in minutes.
  escalation:
    after_minutes: [20, 60]
  kinds:
    birthday: {lead_days: [14, 7, 2, 0], priority: 1}
    event_soon: {lead_minutes: 30, priority: 0}
    work_item: {priority: 1}
  # The acknowledgement link in an email or ntfy message. Empty means no link.
  public_url: ""
```

Secrets come from the environment, never this file, per the project rule. The
channels are the owner's choice: push, ntfy and email are all on, and the
console inbox is always available. A channel with its address or topic missing
disables itself at startup with a warning instead of pretending to be a rung —
a configured-but-unreachable channel is worse than no channel, because it makes
the ladder look longer than it is.

## 13. Verification

Automated:

```bash
cd apps/api && .venv/bin/python -m pytest \
  tests/test_phone_recurrence.py tests/test_phone_birthdays.py \
  tests/test_proactive_plan.py tests/test_proactive_policy.py \
  tests/test_proactive_delivery.py -q
```

- the eleven real rows expand to their expected dates (§1 shows five of them),
  as a table-driven test rather than a spot check;
- a one-off and a yearly row for the same person produce one ambiguous subject
  that refuses to schedule until resolved;
- a fake clock at 2026-12-06 plans T-14/T-7/T-2/T-0 at the right instants;
  re-planning twice creates no duplicates; a corrected date supersedes the old
  occurrence;
- quiet hours hold a P1 and release it at 07:30; the fifth P1 of a day becomes a
  digest entry; a P0 same-day item bypasses;
- an unacked send escalates on schedule and stops at the ack; an ntfy `200`
  does not count as an ack;
- the digest lists what is unacked and nothing twice.

Live:

1. `POST /proactive/scan` against a copy of `data/winnie.db`; `GET
   /proactive/items` must contain 圆圆 12-12 and 12-20, 舅舅 2027-01-28, and
   must **not** contain 圆圆 on 2026-11-12.
2. Ask the same chat question as the incident; the answer names 12-20 with its
   source and no tool-less "not found".
3. Acknowledge from the console, then from the phone with a real notification
   action; the ladder stops both times.

## 14. Risks

| Risk | Mitigation |
|---|---|
| **Noise.** The dominant failure mode: a correct alert sent too often. | Digest by default, four interrupts a day, per-subject mute, feedback that changes the lead time. |
| **Wrong dates.** Lunar vs Gregorian, one-off vs yearly, a renamed person. | Classification + tests on real rows; ask once when ambiguous; the override file is read by chat too. |
| **Hallucinated guidance.** | Template fallback; dates only from evidence; the model's sentence is labelled as a suggestion. |
| **Privacy.** Bodies on a lock screen; email and ntfy leave the machine. | Per-channel content policy, short bodies, a self-hosted ntfy if the content must not leave, location use off by default. |
| **Cost.** A model call per item. | Compose once per item, cache, cap per day, no model in the detector or the dispatcher. |
| **Reliability.** The API process must run for a send. | Durable item rows, catch-up collapse, digest as the backstop. |

## 15. Non-goals

- Buying anything, or messaging a third party without a per-send confirmation.
- Reading WeChat (fragile, against its terms) or any account without an API.
- Listening to the microphone for triggers; the wake word is the only audio path.
- Medical, legal or financial advice: Winnie points at the owner's own data and
  at a professional, it does not decide.

## 16. Trying it today

Prerequisite: the current debug APK
(`apps/android/app/build/outputs/apk/debug/app-debug.apk`), signed in, with
"Alerts & background" enabled on the phone.

1. **Console → Inbox → 测试提醒 / Test reminder.** It creates a *real* item, so
   the notification carries an `item_id`, and sends it through the first
   available channel (push when the app is connected). The Inbox row shows each
   attempt and its receipt or error.
2. **On the phone**, a proactive notification has two buttons: **收到** (ack)
   and **一小时后** (snooze 60 minutes). 收到 clears it and stops the ladder;
   一小时后 moves it out by an hour. Tapping the body opens the app.
3. **ntfy**: subscribe to the topic named by `WINNIE_NTFY_TOPIC` in
   `apps/api/.env`. The message's click action is the signed acknowledgement
   URL. On a public ntfy server the **topic name is the only credential** —
   anyone who knows it can read the messages — so keep it random and out of
   git, or self-host.
4. **Email**: the 126 mailbox needs its **authorization code**, not the login
   password, in `WINNIE_SMTP_PASSWORD`. Without it the channel reports the
   missing variable and the ladder skips it. `POST /api/v1/proactive/test`
   with `{"channel": "email"}` tests that rung alone.
5. **Escalation**: send a test and leave it. At +20 minutes the next rung is
   tried, at +60 the next, and what is left joins the digest.
6. **Quiet hours** are bypassed by a manual test on purpose; real items are
   held until 07:30.

## 17. Decisions taken

Answered by the owner on 2026-11-01:

1. **Channels: push, ntfy and email, all three.** The app carries the rich item
   and the conversation; ntfy is the fast bare notification for when the app
   process is dead; email is the durable rung that survives everything. Which
   ntfy server (public or self-hosted) and which mailbox are configuration, not
   design — a self-hosted ntfy keeps the content on the owner's infrastructure
   and is the option this project would prefer.
2. **Standing approval for reminders, no per-item confirmation.** Winnie may
   create its own calendar reminders and alarms (dedicated `winnie` calendar, at
   most two a day, P0/P1 only). The gate stays on outward messages and
   irreversible actions — see §6 for the scope and the reason.
3. **First version: birthdays and the work week.** The lunar fix, contact
   birthdays, the chat answer and the memory guard land first; the engine,
   channels and schedule source follow in the same slice.

Still open, and they do not block step 1:

- the ntfy server and topic, the mailbox and its app password (configuration);
- the interrupt budget and quiet hours from §12 — start with them and tune from
  two weeks of inbox history;
- whether Winnie may ever send a message to another person without a
  confirmation; today the answer is no, and one explicit switch would change it.
