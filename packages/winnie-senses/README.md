# winnie-senses — room capture (P0)

The camera half of Winnie's senses layer. One process, `winnie-roomd`, owns the
USB camera and is the only thing that touches it; everything else asks it over
loopback HTTP. That is not ceremony: a UVC device is exclusive, and the first
version of this problem was a `Device or resource busy` from every program that
was not the one holding the device.

This is **P0** of the plan in [`docs/senses.md`](../../docs/senses.md): capture
and retention only. No detection, no interpretation, no notifications yet.

## What it does

- captures **640×480 MJPEG at the device rate** (25–30 fps) and evaluates
  **3 frames per second**;
- compares each evaluated frame with the last one it kept, on a 160×120
  grayscale signature (mean absolute difference, 0–255);
- **skips frames below the threshold** — a static room is not written at all;
- writes **one Matroska file per wall-clock minute**, containing only the kept
  frames, with their real timestamps (variable frame rate);
- **drops the minute entirely** when no frame cleared the threshold;
- keeps an append-only `index.jsonl` so the console can list what exists without
  walking the tree.

Measured on this camera: a perfectly still frame scores ~0.5 (JPEG noise floor),
a seated person shifting in a chair ~1.4-1.6, and walking through the room
10-30. The default threshold is **3.0**, so the quiet band is skipped and real
movement is kept.

## Run

```sh
# foreground, from the repo root
PYTHONPATH=packages/winnie-senses/src python3 -m winnie_senses.roomd

# or
make senses
```

Output lands in `data/camera/` (gitignored):

```
data/camera/
  segments/2026-11-07/1625.mkv   # only minutes that changed
  index.jsonl                    # one line per saved minute
  tmp/                           # frame staging, removed after mux
  failed/                        # frames kept when a mux fails
```

## API (127.0.0.1:8077)

| Route | Does |
|---|---|
| `GET /health` | `{ok, camera}` |
| `GET /state` | counters: frames seen/scored/kept, static/saved minutes, last score, disk free |
| `GET /snapshot` | latest JPEG as seen, `image/jpeg` |
| `GET /segments?limit=50` | tail of `index.jsonl` |
| `POST /release?seconds=1800` | close the device so a video call can use it; auto-resume after N seconds (omit `seconds` to stay paused) |
| `POST /resume` | take the device back now |

`release` is the answer to the exclusivity problem: when you open a meeting, the
daemon must let go, and it must come back by itself.

## Tuning

| Flag | Default | Meaning |
|---|---|---|
| `--target-fps` | `3` | evaluations per second (the file's nominal rate) |
| `--change-threshold` | `3.0` | mean absolute difference that counts as a change |
| `--out-dir` | `data/camera` | where segments and the index go |
| `--port` | `8077` | loopback HTTP |
| `--duration` | `0` | stop after N seconds, for a bounded test |

If the room records too much, raise the threshold (4–5) or lower `--target-fps`.
If someone sitting still is missed, lower it toward 2.0 — being still *is* the
absence of change, which is the requested behaviour, not a bug.

## Tests

```sh
make senses-test
# or
cd packages/winnie-senses && PYTHONPATH=src python3 -m unittest discover -s tests -t . -v
```

The suite covers the MJPEG splitter (chunk boundaries, resync), the change score
(noise floor vs a person-sized change), and the minute muxer (empty minute → no
file, kept minute → a VFR file whose last frame is stamped at its real offset).
