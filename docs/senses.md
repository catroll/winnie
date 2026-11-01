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
| **On demand** | Winnie decides, or the owner asks | one snapshot or one short clip goes to a vision model; one audio window goes to ASR | 2–5 s with a small local model, 15–30 s with a 3B one ([§6.1](#61-what-the-6900hx-can-actually-do-measured-2026-11-01)) |
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
- No camera in a bathroom; and see below for a room that is also used for
  sleeping.
- Privacy masks over windows and over the neighbours' side of the glass.
- Power over Ethernet if there is any chance of running a cable; a Wi-Fi camera
  that drops for 30 s is a camera that missed the event.

### The study that is also the bedroom

The owner spends most of the day in the study and also sleeps there, which
changes the placement rather than ruling the camera out:

- **The camera covers the desk, not the bed.** Aim it at the chair, the monitor
  and the door; keep the sleeping area outside the frame or behind a Frigate
  privacy mask. A masked bed is invisible to recordings and to `look`, which is
  the point.
- **Night mode.** From bedtime to wake-up (`body=asleep`) the camera is off or
  snapshot-only on an explicit request, recording pauses, and the mic stays
  wake-word-only. The state layer already knows when that is, so the rule is
  written over projections, not over a separate schedule.
- **A physical shutter** on the camera, not only a software switch. The owner
  must be able to make it blind without opening an app.
- Sleep is then a *watch* signal, not a video one. Do not build sleep staging
  on a camera, and do not record the bed to prove someone slept.

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

The owner has one machine — a Ryzen 9 6900HX mini-PC, no discrete GPU — and
wants the camera and the assistant on it. That is viable for **one room**, with
two facts to design around: the iGPU accelerates decode but not detection, and
the VLM shares the same RAM as everything else.

### 6.1 What the 6900HX can actually do (measured 2026-11-01)

| Fact | Measurement |
|---|---|
| iGPU | Radeon 680M, RDNA2, 12 CU, no dedicated VRAM; GTT 13.3 GiB of the 26 GiB system RAM |
| Vulkan | Mesa RADV present (`libvulkan_radeon.so`, `radeon_icd.json`), so llama.cpp's Vulkan backend is the realistic path; ROCm support for Rembrandt APUs is unofficial at best |
| Hardware decode | verified: 720p H.264 decoded through VAAPI at 44× realtime (`ffmpeg -hwaccel vaapi -hwaccel_device /dev/dri/renderD128`), so Frigate's decode is fine |
| Memory bandwidth | **~46 GB/s** aggregate read (8 threads, 2 GiB buffer); 31–33 GB/s from one thread. DDR5-4800 dual channel peaks at 77 GB/s, so the measured efficiency is normal |
| Free RAM at measurement | 26 GiB total, ~19 GiB already used by the desktop, the IDE and the services, **no swap** |
| Free disk | 62 GB on `/` (93 % used) — recordings and models do not fit there |

Two consequences that decide the architecture:

1. **Frigate cannot use the AMD iGPU for object detection.** Its accelerators
   are OpenVINO (Intel), TensorRT (NVIDIA), Coral/Hailo/MemryX add-ons, and CPU
   ONNX. On this box detection runs on the 8 Zen 3+ cores. For one camera at
   detect 5 fps that is affordable; for four it is not, and the CPU is also what
   the assistant and the IDE use.
2. **A vision model runs, but slowly, and competes for RAM.** Token generation
   is bandwidth-bound, so the ceiling is `46 GB/s ÷ model size`.

| Model (Q4) | Weights + vision encoder | Decode ceiling | Realistic decode | Prefill one 1280×720 frame | `look` end to end |
|---|---|---|---|---|---|
| SmolVLM-500M | ~0.5 GB | ~90 tok/s | 25–40 tok/s | < 1 s | **2–5 s** |
| Moondream 2 (1.8B) | ~1.4 GB | ~33 tok/s | 12–20 tok/s | 2–4 s | **6–12 s** |
| Qwen2.5-VL-3B | ~2.6 GB | ~18 tok/s | 6–10 tok/s | 4–8 s | **15–30 s** |

Those are estimates from the measured bandwidth and the 680M's peak (12 CU ×
128 lanes × 2.4 GHz ≈ 7.4 TFLOP/s FP16, of which a Vulkan backend realistically
reaches 20–35 %); they are not a benchmark. The shape is what matters: a small
model answers in seconds, a 3B model takes half a minute, and neither can watch
video continuously.

**Therefore:** `look` uses a small local VLM and a downscaled frame (640–960
px), and a 3B model is a fallback for a hard question, not the default. Rich
descriptions stay a cloud call the owner explicitly triggers, or wait for a
discrete GPU. Also note that a first-token latency of 10–30 s is invisible in a
push notification but is not a conversation.

### 6.2 Parts

| Part | ≈ Price | Why |
|---|---|---|
| the existing 6900HX machine | ¥0 | decode + CPU detection for one camera; the VLM runs on demand |
| 2 TB surveillance HDD (WD Purple / SkyHawk) | ¥350–450 | 24/7 write endurance; the system disk is at 93 % and desktop drives die early here |
| an N100 mini-PC (only when a second room is added) | ¥800–1,200 | Quick Sync decode + OpenVINO detection takes Frigate off the workstation |
| 4-port PoE switch (only if PoE cameras) | ¥150–250 | one cable per camera |
| Coral USB (optional) | ¥500–700 | lowers CPU; not needed for one camera |

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
    - /mnt/recordings/frigate:/media/frigate   # the 2 TB disk, LUKS
    - /etc/localtime:/etc/localtime:ro
  ports: ["5000:5000", "8554:8554", "8555:8555/tcp"]
  devices: ["/dev/dri:/dev/dri"]      # VAAPI decode on the 680M
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
| **One room, this server** | 1 × Tapo C220, 2 TB HDD, spare phone as the mic | ¥600–750 | the study, end to end, on hardware the owner already has |
| **Two rooms** | 2 × Tapo C220, N100 mini-PC, 2 TB HDD, 2 × ESP32-S3 | ¥2,300–2,900 | ambient events, `look`, `listen`, room voice, Frigate off the workstation |
| **Four rooms, PoE** | 4 × Reolink PoE, PoE switch, N100, 4 TB, 3 satellites | ¥4,500–6,000 | the whole flat, no Wi-Fi dependency |

## 10. Phases

| Phase | Scope | Acceptance |
|---|---|---|
| S0 | spare phone as the first room node: wake word, dialogue, TTS reply, snapshot | say "winnie" in the room and get an answer from that speaker; `look` returns a description |
| S1 | Frigate on the existing server, one camera in the study, person events into `winnie-state` | "is anyone in the study" is answered from the state, not from a tool call the model had to invent |
| S2 | zones, `look` with a small local VLM, `clip`, genai review summary | "what happened in the study this afternoon" is answered from review items; `look` returns in under 10 s |
| S3 | ESP32 node, mic-gated `listen`, winnie-initiated speech, night mode | a state transition speaks in the room; at night the camera is off and nothing is recorded, and it comes back on when `body` says awake |

## 11. Open questions for the owner

1. **Which Huawei watch**, and is it paired to Huawei Health on one of the two
   phones? That decides whether sleep data needs no purchase at all.
2. **How many rooms**, and can Ethernet be run to them? PoE changes the purchase
   list. One room is settled; a second is what forces a decision on Frigate's
   machine.
3. **Who else lives in the flat**, and how should the camera and mic be
   disclosed to them?
4. **Where does the camera sit in the study**, so the desk is in frame and the
   bed is not?
5. **Cloud VLM or not**: with the 6900HX, a small local model is fine for "is
   someone there / at the desk" and poor for reading a screen. Is a cloud call
   for the hard questions acceptable, and if so with which provider?
