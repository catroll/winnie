# Senses: room cameras and microphones

Status: **proposal**, nothing implemented. Last updated: 2026-11-01.

The owner wants cameras and microphones in the rooms as Winnie's eyes and ears,
and contact that does not stop when the phone is in a pocket. This document is
the plan for that layer: what "always on" can actually mean, which hardware to
buy, how the streams reach the assistant, and what has to be true before a
camera is plugged in.

Companion document: [state.md](./state.md) describes what Winnie *knows* —
`place`, `body`, `focus`, `availability`. This one describes how it *perceives*,
and the two meet at the state signals.

## 1. What "24/7" has to mean

A model cannot watch video for 24 hours, and a system that pretends to is
either unaffordable or dishonest. The useful decomposition is three modes:

| Mode | Trigger | What happens | Latency |
|---|---|---|---|
| **Ambient** | always | the camera and the mic run locally; a detector turns "something happened" into an event; the event becomes a state signal | sub-second to the event, seconds to the state |
| **On demand** | Winnie decides, or the owner asks | one snapshot or one short clip goes to a vision model; one audio window goes to ASR | 2–8 s |
| **Conversation** | the owner says "winnie" | wake word opens a voice turn in that room, reply spoken there | 3–6 s |

Everything else — recording, retention, review — is storage, not attention.

## 2. What is realistic, and what is not

| Claim | Reality |
|---|---|
| "Winnie sees the room all day" | It sees events and, when it asks, a frame. Continuous video understanding is not a product; a 2 fps stream to a VLM costs more than a human assistant |
| "Winnie hears everything" | The mic hears everything *locally*; audio leaves the room only after a wake word or a sound event. Continuous upload is a bill and a permanent recording of every conversation in the house |
| "It knows who is in the room" | With face recognition, technically yes (Frigate 0.18 has it). It is biometric data about household members and guests; **off by default**, a later opt-in |
| "It can talk to me anywhere" | In a room with a speaker, yes. Outdoors, the phone is the node |

The honest promise is: **the house is instrumented, the assistant is reachable
in it, and it can look or listen when there is a reason.**

## 3. Architecture

```
 cameras ──RTSP──► go2rtc ──► Frigate ──┬─ events (person, motion, sound) ──► MQTT
                                        ├─ recordings / snapshots (disk)      │
                                        └─ /api (snapshot, clip, review)      │
                                                                              ▼
 room mics ──wake word──► audio stream ──► ASR ──► assistant ──► TTS ──► room speaker
     │                                                                       
     └── local VAD: nothing leaves the room until "winnie" or a sound event  

                                   winnie-api
                          ┌────────────┴────────────┐
                          ▼                         ▼
                   winnie-state (signals)      senses tools
                   room.occupancy,             look(room)
                   room.sound, room.speech      listen(room, s)
                                                speak(room, text)
```

- **Frigate** is the NVR: RTSP in, local object detection, zones and masks,
  recording with retention, snapshots, an HTTP API and MQTT. Version 0.18 also
  has a generative-AI hook that can summarise a review item through any
  OpenAI-compatible endpoint — which the Winnie gateway already is.
- **go2rtc** (bundled with Frigate) restreams one camera connection to many
  readers and serves the snapshot path Winnie needs.
- **The room node** is the mic and speaker, not the camera: a spare phone, an
  ESP32-S3 satellite, or a Raspberry Pi.
- **Winnie's server never opens a connection into the house.** Frigate pushes
  events and the room nodes push audio, the same edge-to-hub direction the rest
  of the system already uses.

## 4. Cameras

Requirements: RTSP or ONVIF with a **local account**, 2K or better, usable in
low light, PoE or stable Wi-Fi, and no mandatory vendor cloud. A camera that
only works through its vendor's app is disqualified — it is someone else's eye.

| Camera | ≈ Price | Stream | Pros | Cons |
|---|---|---|---|---|
| Tapo C220 (indoor pan/tilt, 2K) | ¥220–280 | RTSP, local camera account | cheapest credible option; pan/tilt covers a room | ONVIF is not on every Tapo model — Frigate only needs RTSP |
| Tapo C120 (indoor, 2K) | ¥180–240 | RTSP, local camera account | fixed, discreet, cheap | same |
| Reolink E1 Pro (indoor pan/tilt, 2K) | ¥280–360 | RTSP + ONVIF | good night image, stable firmware | Wi-Fi only |
| Reolink RLC-520A (PoE, 5MP) | ¥350–450 | RTSP + ONVIF | PoE: one cable, no Wi-Fi dropouts, no battery | outdoor bullet form factor; needs a PoE switch |
| spare Android phone | ¥0 | Winnie app | already has the wake word, a speaker and a camera | not a 24/7 video device; ROM may kill it |

Start with **two cameras in two rooms that matter** (the study and the room
where the owner spends evenings), not with the whole flat. Placement rules that
matter more than the model:

- The lens looks at the *room*, not at a doorway only; a person walking in
  should be visible for longer than one frame.
- No camera in a bathroom, and none pointed at a bed. That is not a legal
  footnote, it is the difference between a tool and a surveillance system.
- Privacy masks over windows and over the neighbours' side of the glass.
- Power over Ethernet if there is any chance of running a cable; a Wi-Fi camera
  that drops for 30 s is a camera that missed the event.

## 5. Microphones and speakers

Audio is the harder half. A far-field mic in a room picks up everything,
including people who did not agree to be recorded, so the default is
**local processing, gated release**:

| Node | ≈ Price | Wake word | Speaker | Fit |
|---|---|---|---|---|
| spare Android phone | ¥0 | yes — sherpa-onnx KWS, already implemented ([voice-wake.md](./voice-wake.md)) | yes (native TTS) | the first room node, no purchase, validates the whole loop |
| ESP32-S3-BOX-3 | ¥250–350 | microWakeWord / ESPHome | yes | purpose-built, always powered, no ROM killing the app |
| ESP32-S3 Atom Echo | ¥120–180 | microWakeWord | tiny | cheapest per room; audio quality is the compromise |
| Raspberry Pi 5 + USB conference mic | ¥500–700 | yes | yes (with a speaker) | most flexible; can run ASR locally |
| Home Assistant Voice PE | ~$59 + import | yes | yes | polished, but a second ecosystem to own |

**Start with the spare phone.** It exercises the path that matters — wake word,
streamed turn, ASR, reply — with no purchase, and it proves the latency before
any money is spent. Then buy one ESP32-S3 per additional room; they are
appliance-like and do not need a screen.

Text to speech is the missing piece server-side: the console speaks through the
browser's `speechSynthesis`, which a room node cannot use. Three options, in
order of preference:

1. **the node speaks**: Android `TextToSpeech` on a phone node — free, offline,
   good Chinese voices, no new dependency;
2. **Piper** on the server — local, fast, Apache-2.0, voice quality is adequate;
3. a cloud TTS vendor — best quality, and every reply leaves the house. Last.

## 6. The hub

Frigate needs a machine that is on 24/7 and is not the workstation. Decoding
and detecting continuously beside a development environment is how both become
unreliable.

| Part | ≈ Price | Why |
|---|---|---|
| x86 mini-PC, N100/N150, 16 GB RAM | ¥800–1,200 | Intel Quick Sync decode + OpenVINO detection is enough for 3–4 cameras at detect 5 fps |
| 2 TB surveillance HDD (WD Purple / SkyHawk) | ¥350–450 | 24/7 write endurance; desktop drives die early here |
| 4-port PoE switch (only if PoE cameras) | ¥150–250 | one cable per camera |
| Coral USB (optional) | ¥500–700 | lowers CPU; not needed at 3 cameras on N100 |

Storage arithmetic, so the disk is sized before the first clip is written: a
2K camera at 2 Mbps is ~0.9 GB/hour, ~21 GB/day. Four cameras for 14 days is
~1.2 TB; two cameras for 7 days is ~300 GB. Continuous recording of every room
is mostly unwatched — **record on motion, keep 7 days, keep events 30 days** is
the sane default.

Sketch, not a final compose file:

```yaml
frigate:
  image: ghcr.io/blakeblackshear/frigate:0.18
  shm_size: "256mb"
  volumes:
    - ./config:/config
    - ./media:/media/frigate
    - /etc/localtime:/etc/localtime:ro
  ports: ["5000:5000", "8554:8554", "8555:8555/tcp"]
  devices: ["/dev/dri:/dev/dri"]      # Quick Sync
  environment:
    FRIGATE_MQTT_HOST: mqtt
    FRIGATE_GO2RTC_RESTART: "1"
```

`detect` at 5 fps on a 1280×720 substream, `record` on the main stream, and one
`zone` per room. The generative-AI hook points at the Winnie gateway
(`http://<api>:8000/<route>`, a Winnie gateway key), so a review summary is a
local log entry and a vendor call only when it runs.

## 7. How it reaches Winnie

**Signals** (new kinds in `winnie-state`):

| Signal | Source | Meaning |
|---|---|---|
| `room.presence` | Frigate `person` in a zone | someone is in `study` / `living_room` |
| `room.motion` | Frigate motion | movement without a recognised object |
| `room.sound` | Frigate audio detector | speech, a scream, glass breaking, a door |
| `room.speech` | room node VAD + ASR | what was said after a wake; a transcript, not audio |
| `room.light` | Frigate `brightness` or the camera's own meter | lights on/off, dusk |

**Tools** (one `senses` skill):

| Tool | Does | Guard |
|---|---|---|
| `look(room)` | one JPEG from go2rtc, resized, EXIF stripped → vision model → description | the room must be enabled; no face identity by default |
| `listen(room, seconds)` | one bounded audio window → ASR → transcript | only on explicit request or a sound event; a hard cap (≤ 30 s) |
| `speak(room, text)` | TTS to that room's speaker | no speaking into a room while `body=asleep` unless urgent |
| `clip(room, from, to)` | a stored clip for review | bounded, and only clips Frigate already kept |

**Vision** is a new model purpose. `winnie-llm` today speaks text only
(`chat.py` assembles `content` as text parts), so this is a real addition: an
OpenAI-compatible `image_url` part, a `vision` binding in the AI providers page,
and a per-room policy for what resolution and what masking is applied before the
frame leaves the server. A local VLM is the privacy answer and needs a GPU;
without one, the choice is a cloud VLM or no `look` tool.

**Triggers** reuse the watch pattern: `room.sound = door` between 22:00 and
07:00 → notify; `room.presence = study` after 20:00 → "you're still working,
your last meeting is tomorrow 09:00"; `room.speech` matching "winnie" → the
voice turn. The morning brief can say "someone left at 07:40" because the camera
saw it, not because the phone did.

## 8. Security and privacy

A camera is the highest-value target in a home network. These are requirements,
not suggestions:

- **No vendor cloud account.** Local account on the camera, RTSP only. If a
  camera cannot be viewed without its vendor's app, it is not bought.
- **Isolated network.** Cameras on their own VLAN/SSID with **no WAN egress**
  (a router rule, verified by watching the camera try to reach its vendor), and
  Frigate on the LAN only. Remote access is WireGuard/Tailscale, never a port
  forward.
- **Encryption at rest.** The recording volume is LUKS-encrypted; the state and
  conversation databases already deserve the same and do not have it
  ([phone.md](./phone.md) §8).
- **Retention.** Continuous 7 days, events 30 days, audio never stored unless a
  clip is explicitly kept. Retention is configured, not implied.
- **Kill switches.** Per-room camera and mic enable; a physical mic mute (the
  ESP32 button, or unplugging the phone); a visible indicator on the node.
- **Household consent.** Anyone living in the flat knows the cameras exist and
  where. Audio of visitors is the sensitive part; a notice at the door is the
  minimum.
- **Face recognition off.** It is biometric data with its own legal regime, and
  it is not needed for "is someone home".
- **The owner is the only account.** The console already has one account model;
  cameras must not become the first multi-tenant surface.

## 9. Cost

| Configuration | Parts | ≈ Total | What it proves |
|---|---|---|---|
| **Zero** | spare phone + existing server | ¥0 | wake word, voice turn, speak, snapshot from the phone camera |
| **Two rooms** | 2 × Tapo C220, N100 mini-PC, 2 TB HDD, 2 × ESP32-S3 | ¥2,300–2,900 | ambient events, `look`, `listen`, room voice |
| **Four rooms, PoE** | 4 × Reolink PoE, PoE switch, N100, 4 TB, 3 satellites | ¥4,500–6,000 | the whole flat, no Wi-Fi dependency |

## 10. Phases

| Phase | Scope | Acceptance |
|---|---|---|
| S0 | spare phone as the first room node: wake word, dialogue, TTS reply, snapshot | say "winnie" in the room and get an answer from that speaker; `look` returns a description |
| S1 | Frigate on the mini-PC, one camera, person events into `winnie-state` | "is anyone in the study" is answered from the state, not from a tool call the model had to invent |
| S2 | two cameras, zones, `look`/`clip`, genai review summary | "what happened in the kitchen this afternoon" is answered from review items |
| S3 | second room node, mic-gated `listen`, winnie-initiated speech | a state transition speaks in the room, and nothing is recorded when it does not fire |

## 11. Open questions for the owner

1. **Which Huawei watch**, and is it paired to Huawei Health on one of the two
   phones? That decides whether sleep data needs no purchase at all.
2. **Is there a GPU** (desktop or server) that can host a local vision model, or
   is a cloud VLM acceptable for `look`?
3. **How many rooms**, and can Ethernet be run to them? PoE changes the purchase
   list.
4. **Who else lives in the flat**, and how should the camera and mic be
   disclosed to them?
5. **Where does the mini-PC live**, and is there a spot with power and a cable
   to the router?
