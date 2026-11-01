# Personal state: what Winnie can know, and how it gets there

Status: **proposal**, nothing implemented. Last updated: 2026-11-01.

The owner asked for Winnie to know four things without being told: where they
are, what their body is doing, whether they are asleep, and what they are doing
right now — acquired "completely on its own". This document is the answer: what
is measurable, through which channel, with which hardware, and how the result
reaches the assistant every turn instead of a tool it may forget to call.

## 1. "Proactive" is four separate requirements

Each fails differently, and a design that satisfies one is not evidence for the
others:

| # | Requirement | What satisfies it | What it looks like when missing |
|---|---|---|---|
| 1 | The owner does nothing per **reading** | collectors push continuously; no "check now" button | the data is hours stale because a sync only runs when the app is open |
| 2 | The owner does nothing per **question** | state is in the system prompt on every turn | "what am I doing?" is answered by calling a tool three rounds late, or not at all |
| 3 | The owner does nothing per **notification** | a transition can start a task or push | the assistant only speaks when spoken to |
| 4 | Winnie never **lies** about it | every state carries its timestamp and confidence | "you're at home" from a three-hour-old fix |

Requirements 1–3 are the plumbing. Requirement 4 is the design: a state model
that can say *unknown*, and a prompt that shows freshness first.

## 2. What is measurable, honestly

| Signal | Measurable? | Best source | Honest limit |
|---|---|---|---|
| Which building / neighbourhood | yes | phone location + geofence | indoors a phone falls back to a network fix: 20–200 m |
| Which room | only with infrastructure | camera, BLE beacon, mmWave, Wi-Fi fingerprint | GPS cannot do it; needs tags, sensors or a camera per room |
| Whether anyone is in a room | with a camera | Frigate person detection in a zone | a camera is not a person counter; two people read as "occupied" |
| Gross motion (still / walking / running / cycling / driving) | yes, coarse | accelerometer + step counter, cross-checked by fix speed | cycling vs driving is the classic confusion; ~85–95 % on the coarse classes |
| A workout is in progress | yes with a wearable | heart rate + cadence from a band/ring | from the phone alone it is a heuristic, not a measurement |
| Asleep / awake | partly — phone-only is a guess | screen-off + still + charging + night window; a bed sensor is far better | usually right about bedtime and wake time; unreliable for naps, and it cannot stage sleep |
| Sleep stages and quality | only a body sensor | under-mattress sensor, ring, or band | no phone can do this; do not promise it |
| "What I'm doing" (category) | inferable, not measured | foreground app + PC window + calendar + room | high confidence for "coding / in a meeting / watching video", low for "reading / thinking" |
| Intent ("why", "what next") | **not measurable** | — | must be asked, or inferred and labelled as a guess |

The last row is the one to hold on to. Everything before it is context; intent
is self-report. Any design that implies Winnie *knows* the owner is writing a
document, rather than *sees an editor focused for 40 minutes*, is overselling.

## 3. The state model

Two layers, and the split is what keeps the assistant honest:

**Raw signals.** One row per observation: `kind`, `source`, `ts`, `payload`,
`confidence`. Append-only, coarse retention, never rendered directly.

**Derived projections.** Four facts the assistant actually reads, each a
fusion of signals with a freshness stamp:

| Projection | Values | Fused from |
|---|---|---|
| `place` | `home`, `office`, `commute`, `gym`, `other:<name>`, `unknown` + room | location fixes, geofences, beacons, mmWave, Wi-Fi |
| `body` | `asleep`, `waking`, `awake`, `walking`, `running`, `cycling`, `driving`, `workout`, `unknown` | accelerometer/steps, fix speed, heart rate, sleep session |
| `focus` | `working`, `in_meeting`, `reading`, `eating`, `leisure`, `away`, `unknown` | foreground app, PC window, calendar, room, idle time |
| `availability` | `available`, `busy`, `asleep`, `driving`, `dnd` | the three above + calendar |

A projection is recomputed on read from signals younger than its TTL. When the
newest signal is older than the TTL the projection is `unknown` with a reason,
not the last guess carried forward.

Suggested TTLs, chosen so that a phone that died an hour ago does not claim the
owner is still at the office:

| Projection | TTL | Why |
|---|---|---|
| `place` | 20 min stationary, 3 min moving | a fix while walking ages faster than one at a desk |
| `body` | 10 min | motion state changes faster than place |
| `focus` | 5 min for PC, 15 min for phone | window focus is the most volatile signal |
| `availability` | 15 min | it is a policy, not a measurement |

## 4. Acquisition channels

The direction is always edge → hub. The server cannot reach into a sleeping
phone (the same constraint [push.md](./push.md) starts from); it can only poll a
vendor cloud or receive what a collector sends.

### 4.1 The phone (no new hardware, fastest path)

The Android client already uploads calendar, calls, SMS, contacts,
notifications, a five-minute location fix, apps and the device profile
([phone.md](./phone.md)). Four new scopes cover most of the question:

| Scope | Source | Permission / access | Note |
|---|---|---|---|
| `motion` | accelerometer + step counter, classified on device | no permission for the accelerometer; `ACTIVITY_RECOGNITION` for the step counter | no Play Services here: this app has no GMS dependency and the owner's phones are Huawei, so `ActivityRecognitionClient` (and its transitions API) is not an option. `ACTIVITY_RECOGNITION` is a normal runtime permission the ROM grants; what is missing is the classifier, so classify locally and cross-check with fix speed. |
| `screen` | `UsageStatsManager.queryEvents` + screen on/off/unlock broadcasts | `PACKAGE_USAGE_STATS` (a Settings grant, disclosed as such) | upload app id, category and interval — not an event log |
| `place` | `LocationManager`, enter/exit computed in-app | already granted | adaptive: compare each fix against the home/office polygons instead of the GMS `GeofencingClient` (unavailable), request a fix on motion transitions, keep the five-minute bucket as fallback |
| `sleep` | screen-off + still + charging + night window | none | a derived interval marked `guess`; superseded by a body sensor when one exists |

The accelerometer needs no permission; the step counter needs `ACTIVITY_RECOGNITION`,
which is a normal runtime permission the ROM grants. What is missing without
Play Services is the *classifier*, not the permission — hence a local one. The
same absence is why `SleepClassify`/`SleepSegment` from
`play-services-location` cannot be the sleep source: those APIs do not exist on
this phone.

### 4.2 The workstation agent (the strongest "what am I doing" signal, free)

For a developer, the editor window is a better answer than any wearable. A
small daemon on the workstation POSTs state every 10 s:

- idle seconds and locked/unlocked (`org.freedesktop.login1` `IdleHint` /
  `LockedHint` over D-Bus);
- active window class and title (`xprop`/`wmctrl` on X11; `swaymsg -t get_tree`
  or `hyprctl activewindow` on Wayland — the compositor decides, so this is
  per-desktop code);
- whether a call is in progress (a PipeWire/PulseAudio capture stream owned by
  a browser or meeting app);
- optionally the terminal's cwd and git branch, from a shell hook.

Window titles are the one signal that can contain anything, so the agent
classifies before upload: an allowlist of app classes reports the title, a
denylist (password managers, banking) reports only the class, everything else
reports class + a category. Uploading raw titles for every window is a
surveillance log, not a state.

### 4.3 The watch (the only reliable sleep source, and already owned)

The owner owns a Huawei watch and two Huawei phones, so the shortest path to
real sleep data needs no purchase: **Huawei Health Kit, device side**. The
watch writes into Huawei Health, Health Kit reads Huawei Health's store, and the
Winnie app reads Health Kit. The app never talks to the watch, and no vendor
cloud is involved in the read.

| Scope | Data |
|---|---|
| `HEALTHKIT_SLEEP_READ` | sleep sessions and stages |
| `HEALTHKIT_HEARTRATE_READ` | heart rate, resting heart rate |
| `HEALTHKIT_STEP_READ` | steps |
| `HEALTHKIT_ACTIVITY_RECORD_READ` | workouts |
| `HEALTHKIT_OXYGEN_SATURATION_READ` | SpO₂ |
| `HEALTHKIT_STRESS_READ` | stress |

What it needs, and what can block it:

- AppGallery Connect registration: package name, signing-certificate SHA-256,
  the Health Kit service enabled, HMS Core on the phone, a Huawei account.
- The owner authorizes each scope in Huawei Health's own dialog, and can revoke
  it there; a refusal must surface in the app, not as an empty sleep history.
- **The risk is onboarding, not the data path.** Whether a sideloaded build with
  a registered debug certificate is accepted for these scopes has to be tested
  on the device before anything is built on it — it is the riskiest integration
  in this plan, so spike it first.
- Fallback: the Health Kit cloud REST API (same developer account, OAuth,
  server-side polling). Heavier to onboard, but independent of the app's
  signing certificate.

Alternatives, only if the watch path fails or a second person needs tracking:

| Route | Data | Cost | Catch |
|---|---|---|---|
| Band + **Gadgetbridge** auto-export | sleep sessions, HR, steps, workouts | band ~¥250 | no cloud; the bridge must parse Gadgetbridge's exported database ([docs](https://gadgetbridge.org/internals/automations/auto-export/)) |
| **Withings Sleep Analyzer** under the mattress | sleep stages, HR, bed presence | ~¥1,000–1,500 | nothing to wear; new accounts need Withings+ to share with a free-plan app ([plans](https://static.withings.com/content/api/plans/plans.html)) |
| **Oura Ring** | best staging, HR, temperature | ring + subscription | the API refuses accounts without an active membership ([Oura](https://support.ouraring.com/hc/en-us/articles/4415266939155-The-Oura-API)) |

**Health Connect is not the path here.** It is built into Android 14+, and on
13 and below it is a Google Play app; on a Huawei phone without Play services
it is at best a sideload experiment. A design that depends on it would be a
design that works on a phone the owner does not carry.

### 4.4 Room sensors

- **BLE beacons** (iBeacon/Eddystone, ~¥15–40 each) placed in each room, scanned
  by the phone: gives "which room" at 2–5 m, no hub needed, works with the app
  already installed. Battery tags need replacing once a year.
- **mmWave presence** (~¥100–700 per room): occupancy without the phone, and
  "in bed" when aimed at the bed. It reports presence, not sleep stages, and
  fans/moving curtains are the known false-positive source.
- A **metering smart plug** (~¥30–60) on the desk: monitor on means the owner
  is probably at the desk, which is a coarse but free corroboration.

These publish to the hub (HTTP or MQTT). Home Assistant is acceptable as a
*collector* for Zigbee/BLE devices; it must not become the model of the world,
or Winnie inherits a second source of truth and a second failure mode.

Cameras and microphones are the other room sensor, and they are planned in
[senses.md](./senses.md) because their installation, retention and security
rules are a different problem: one BLE tag cannot record a conversation.

### 4.6 More phone sensors

The phone carries a dozen context signals that need no purchase and no vendor
SDK. They are corroboration, not conclusions — each is one bit, and several are
wrong indoors — but together they sharpen `body` and `focus` a lot:

| Sensor / API | Infers | Note |
|---|---|---|
| accelerometer, gyroscope | still vs moving, posture, gait | no permission for raw sensors; classify on device |
| step counter / detector | walking, running cadence | needs `ACTIVITY_RECOGNITION` |
| `TYPE_LIGHT` | in a pocket, room lit, night | coarse, but excellent for "screen in pocket vs on a table" |
| proximity | phone at the ear (a call), in a pocket | |
| barometer | floor, stairs, elevation change | home vs office floor, and "went downstairs" |
| magnetometer | heading, docked in a car mount | |
| connected Bluetooth devices | in the car, headset on, near the watch | the strongest zero-permission context signal |
| battery and charging | at a desk, charging overnight, unplugged and moving | |
| screen / keyguard / app usage | `focus`, "what am I doing" | `PACKAGE_USAGE_STATS` |
| audio playback and recording state | in a call, watching a video, music | `AudioManager` callbacks |
| Wi-Fi SSID/BSSID | building, and a room fingerprint | location permission |
| cell tower id | coarse movement between areas | |
| significant motion | wake the collector at near-zero cost | |

The barometer and Bluetooth lists are the two that surprise people: a floor
change is what distinguishes "at the office" from "at the office's café", and a
car's Bluetooth is a more reliable "driving" than any accelerometer classifier.

### 4.5 Self-report, the fallback that labels the rest

A one-tap "what are you doing?" on the phone (and a `state_report` tool) is the
only ground truth for intent, and it is also the label set for evaluating the
inference: when the owner taps "reading" while the fusion said "working", that
pair is a test case. Without it, accuracy claims about `focus` are unfalsifiable.

## 5. Hardware options

Prices are approximate CNY for 2026 and should be checked at purchase. Tiers are
cumulative; each is useful on its own.

| Tier | Item | ≈ Price | What it buys | Before buying |
|---|---|---|---|---|
| 0 | nothing | 0 | `place` (neighbourhood), `body` (coarse), `focus` (phone + PC), `sleep` (guess) | the phone already syncs; the PC is already on |
| 1 | 4–6 BLE beacons | ¥80–240 | room-level `place` at home and office | beacon battery life; one per room plus one for the bag |
| 1 | 1–2 mmWave sensors | ¥200–1,400 | occupancy and bed presence without the phone | zone support (FP2) vs plain presence (Tuya); false positives |
| 1 | metering smart plug | ¥30–60 | desk presence corroboration | which plug ecosystem the owner already has |
| 2 | **the Huawei watch the owner already has** | ¥0 | HR, steps, workouts, **staged sleep** via Health Kit | Health Kit onboarding and scope approval ([§4.3](#43-the-watch-the-only-reliable-sleep-source-and-already-owned)) |
| 2 | cameras + room microphones | ¥2,300–6,000 | ambient presence, `look`, `listen`, room voice | a separate design: [senses.md](./senses.md) |
| 2 | Withings Sleep Analyzer | ¥1,000–1,500 | staged sleep, HR, bed presence, nothing to wear | Withings+ requirement for new accounts; availability in CN |
| 2 | Oura Ring | ring + subscription | best sleep staging | membership is mandatory for API access |
| 3 | 4 × UWB anchors | ¥600–1,200 | 10–30 cm indoor position | overkill unless room automation needs it; WiFi fingerprinting is free first |

**Do not buy**: an EEG headband (staging, not activity), or a "smart mattress"
(the same data as an under-mattress sensor at several times the price). Cameras
and microphones are now in scope, but only under the conditions in
[senses.md](./senses.md) §8 — no vendor cloud, isolated network, no bathroom or
bedroom camera.

The cheapest configuration that answers all four questions is **tier 0 plus the
watch the owner already wears**: the phone supplies place, motion, focus and a
sleep guess; the watch supplies real sleep and workouts; the PC supplies the
"what am I doing" answer; and the room cameras from [senses.md](./senses.md)
are what turn guesswork into observation. Room-level location is the only thing
in this document that genuinely needs new infrastructure, and only if "which
room" matters.

## 6. Architecture in this repository

```
phone scopes ──┐
PC agent ──────┤  POST /api/v1/state/signals
BLE / mmWave ──┤  (batched, idempotent, per-signal ts)
vendor cloud ──┘
                    │
                    ▼
        packages/winnie-state
        signals (TTL) → projection
                    │
   ┌────────────────┼────────────────┐
   ▼                ▼                ▼
state_digest    state_now /      transition triggers
(every turn)    state_history    → scheduler / notify
```

- **`packages/winnie-state`** mirrors `winnie-phone`: it owns the schema, the
  fusion rules, retention and queries, and never imports the host. The host
  adapts it to a `StateGateway` protocol, exactly as `phone/gateway.py` does.
- **Room cameras and microphones** publish `room.*` signals into the same
  ingest; their own pipeline is [senses.md](./senses.md).
- **Ingest** is one endpoint with a batch of signals; each carries its own
  timestamp and source, so a late upload does not rewrite "now". Idempotency by
  `(source, signal_id)`, the same rule phone sync uses.
- **Prompt digest** is a new field beside `phone_digest`
  (`packages/winnie-chat/src/winnie_chat/prompt.py`), about 300 characters,
  freshness first, omitted entirely when no signal is younger than its TTL — an
  empty section beats "unknown" five times, which is the rule `phone_digest`
  already follows.
- **Tools**: one `state` skill with `state_now`, `state_history` and
  `state_report`, scheduled-safe (a brief at wake-up needs it). The first two
  are reads; `state_report` records the owner's own answer and nothing else.
- **Triggers** reuse `AssistantScheduler` and the watch pattern: a rule is a
  predicate over projections plus an action (run a task prompt, or push a
  notice), debounced, with quiet hours. "When I wake, brief me" is the first
  useful rule and the acceptance test for the whole layer.

## 7. Delivery policy

- The digest is always present; the model never needs to decide to look.
- Pushes are held while `body=asleep` or `focus=in_meeting` unless the notice is
  marked urgent, and delivered when the state changes. The push queue is already
  durable across reconnects ([push.md](./push.md)), so holding is a scheduling
  decision, not a new transport.
- Staleness is stated: "as of 12 minutes ago" when a signal is old, never a
  confident sentence built from an aged fix.
- The existing watches get better for free: the weather check can use the
  current place instead of the configured city, and the morning brief can start
  at wake instead of a fixed 07:00.

## 8. Privacy and security

This is the most sensitive dataset in the repository, and it reverses two
exclusions recorded in [decisions.md](./decisions.md) ("health data and
per-app usage statistics are not collected"). That reversal is a decision the
owner makes explicitly, not a side effect of a new scope:

- **Per-signal consent.** One switch per scope, each with its own disclosure, and
  a master switch. Nothing is enabled by default; the phone's `Phone data` screen
  stays the consent surface.
- **Derived facts over raw streams.** Classify motion on the device; upload
  "walking, 09:12–09:20", not 50 Hz accelerometer data. Never upload audio.
- **Retention**: raw signals 30 days, app/window usage 14 days, sleep sessions
  365 days, projections not stored at all (recomputed). Configured, not implied.
- **Local only.** No third-party call for state. Reverse geocoding already
  leaves the server when enabled; that stays off for state, or the place stays
  coordinates + room id.
- **The database is not encrypted today** ([phone.md](./phone.md) §8). Sleep,
  location and app usage in one file raises the stakes: volume encryption before
  the first health signal, not after.
- **Windows titles, SMS bodies and camera transcripts are untrusted data**,
  like every other tool output; the prompt already frames tool results that
  way, and the state digest must follow the same rule.
- **Cameras and room microphones are the largest surface of all**, and they have
  their own requirements — no vendor cloud, an isolated VLAN with no WAN
  egress, encrypted storage, a visible indicator, and no bathroom or bedroom
  camera. See [senses.md](./senses.md) §8.

## 9. Phases

| Phase | Scope | Acceptance |
|---|---|---|
| P0 | `winnie-state` package, ingest, projection, digest, `state_now`, phone `motion`/`screen`/`place`/`sensors`, Health Kit spike, PC agent | ask "where am I", "what am I doing", "did I sleep"; each answer is correct or says unknown with a reason; killing a signal makes it unknown within its TTL |
| P1 | BLE/mmWave, room-level `place`, transition triggers, quiet-hours delivery | "when I get to the office, show my day" fires once per arrival; no push during sleep |
| P2 | Health Kit sleep, HR and workouts | the morning brief reports last night's sleep and starts at wake, not at 07:00 |
| P3 | travel time from current place to the next calendar event; "leave now" | the notice is earlier than the event by the measured travel time, not a fixed 30 minutes |

The room senses (cameras, microphones, `look`/`listen`/`speak`) have their own
phases S0–S3 in [senses.md](./senses.md) §10. They are independent of P0–P3 and
feed the same state signals.

Each phase is independently useful; P0 needs no purchase and answers all four
questions, three of them coarsely.

## 10. Open questions for the owner

1. **Which Huawei watch**, and is it paired to Huawei Health on one of the two
   phones? That decides whether sleep needs no purchase at all.
2. **Is there a GPU** that can host a local vision model, or is a cloud VLM
   acceptable for `look`? See [senses.md](./senses.md) §7.
3. **Which workstation(s)?** This Linux box only, or Windows/macOS too? The PC
   agent is per-OS.
4. **Is "which room" worth ¥100–1,400 of sensors** now that the cameras will
   answer it too, or is home/office/commuting enough?
5. **Retention and encryption**: is a LAN-only, unencrypted SQLite acceptable
   until the production-readiness milestone, or should state wait for it?
