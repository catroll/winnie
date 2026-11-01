#!/usr/bin/env python3
"""Measure what the local capture device actually delivers.

This answers the question a failed voice turn raises, in numbers instead of
guesses: is the input muted, how loud is the room, is the signal a DC offset or
mains hum rather than a voice, and is the owner's speech above the noise floor?
It records twice — a quiet phase and a spoken phase — and reports each.

Usage:

    python3 scripts/check-audio-input.py                 # speak when prompted
    python3 scripts/check-audio-input.py --play clip.wav # speaker -> room -> mic
    python3 scripts/check-audio-input.py --source alsa_input.usb-... --json

Two things this deliberately does not do:

* It does not use the browser. `getUserMedia` with `echoCancellation`,
  `noiseSuppression` and `autoGainControl` changes these numbers a lot — a
  device that shows -20 dBFS of rumble here reads -80 dBFS inside the console,
  because the speech processing removes exactly that rumble. This script is the
  hardware baseline; the console's path on top of it is a separate question.
* It does not fix anything. It prints what it measured and one verdict per
  check, so the next step (raise the gain, move the microphone, replace it) is
  the reader's decision.

The thresholds come from the endpoints that consume this audio:
the endpointer counts a 20 ms frame as speech above -38 dBFS
(`apps/admin/src/lib/voice/endpoint.ts`), and a recording-file ASR needs its
speech roughly 10 dB above the noise to transcribe reliably.

Exit status is 0 when every check passes, 1 when any check fails, 2 when the
measurement could not be taken (no `parec`, no capture source).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import time
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path

RATE = 16_000
FRAME_MS = 20
FRAME = RATE * FRAME_MS // 1_000
# The endpointer's speech floor, in dBFS. Keep in step with endpoint.ts.
SPEECH_FLOOR_DBFS = -38.0
# Below this the input is effectively muted; a healthy room sits near -55.
MUTED_RMS_DBFS = -55.0
# A capture chain at high gain turns its own rumble into a usable-looking level
# while the speech band stays empty. That gap is the check, because a microphone
# that hears nothing and a microphone at +60 dB look identical in the RMS number.
SILENT_BAND_GAP_DB = 25.0
# A DC offset this strong eats headroom and carries no speech at all.
DC_WARN_DBFS = -30.0
# Speech needs this much room above the quiet floor before an ASR can work.
MIN_SPEECH_SNR_DB = 10.0
# Anything closer to 0 dBFS than this is clipped and will transcribe badly.
CLIP_LEVEL = 0.999
CLIP_SHARE_WARN = 0.001


@dataclass
class Measurement:
    seconds: float
    samples: array
    dc: float
    dc_dbfs: float
    rms_dbfs: float
    peak_dbfs: float
    clipped_share: float
    frames: list[float] = field(default_factory=list)
    # The same frames after a 300-3000 Hz band pass. Speech lives there and the
    # rumble that fools a broadband gate does not, so the pass/fail decision is
    # taken on these and not on `frames`.
    speech_frames: list[float] = field(default_factory=list)
    bands: dict[str, float] = field(default_factory=dict)

    @property
    def median_dbfs(self) -> float:
        return _percentile(self.frames, 0.5)

    @property
    def loud_dbfs(self) -> float:
        """The 90th percentile: "how loud does this get when something happens"."""
        return _percentile(self.frames, 0.9)

    @property
    def max_dbfs(self) -> float:
        return _percentile(self.frames, 1.0)

    @property
    def speech_median_dbfs(self) -> float:
        return _percentile(self.speech_frames, 0.5)

    @property
    def speech_loud_dbfs(self) -> float:
        return _percentile(self.speech_frames, 0.9)

    @property
    def above_floor_share(self) -> float:
        if not self.frames:
            return 0.0
        above = sum(1 for level in self.frames if level >= SPEECH_FLOOR_DBFS)
        return above / len(self.frames)

    def as_json(self) -> dict:
        return {
            "seconds": round(self.seconds, 2),
            "dc_dbfs": round(self.dc_dbfs, 1),
            "rms_dbfs": round(self.rms_dbfs, 1),
            "peak_dbfs": round(self.peak_dbfs, 1),
            "clipped_share": round(self.clipped_share, 5),
            "frame_median_dbfs": round(self.median_dbfs, 1),
            "frame_p90_dbfs": round(self.loud_dbfs, 1),
            "frame_max_dbfs": round(self.max_dbfs, 1),
            "above_speech_floor_share": round(self.above_floor_share, 3),
            "speech_band_median_dbfs": round(self.speech_median_dbfs, 1),
            "speech_band_p90_dbfs": round(self.speech_loud_dbfs, 1),
            "bands": {name: round(share, 3) for name, share in self.bands.items()},
        }


@dataclass
class Check:
    status: str  # "ok" | "warn" | "fail"
    message: str


def dbfs(value: float) -> float:
    """Amplitude (0..1) as dBFS, with digital silence floored at -120."""
    return -120.0 if value <= 0 else 20.0 * math.log10(value)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return -120.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def analyse(samples: array, rate: int = RATE) -> Measurement:
    count = len(samples)
    if count == 0:
        raise SystemExit("no audio was captured; is the source suspended?")

    dc = sum(samples) / count
    power = sum(value * value for value in samples) / count
    peak = max(abs(value) for value in samples)
    clipped = sum(1 for value in samples if abs(value) >= CLIP_LEVEL) / count

    def levels(signal: list[float]) -> list[float]:
        return [
            dbfs(math.sqrt(sum(v * v for v in signal[start : start + FRAME]) / FRAME))
            for start in range(0, len(signal) - FRAME + 1, FRAME)
        ]

    return Measurement(
        seconds=count / rate,
        samples=samples,
        dc=dc,
        dc_dbfs=dbfs(abs(dc)),
        rms_dbfs=dbfs(math.sqrt(power)),
        peak_dbfs=dbfs(peak),
        clipped_share=clipped,
        frames=levels(list(samples)),
        speech_frames=levels(speech_band(list(samples), rate)),
        bands=band_shares(samples, rate),
    )


def speech_band(samples: list[float], rate: int) -> list[float]:
    """Keep 300-3000 Hz only, with two second-order biquads.

    One RBJ section each side leaves the stop band near -30 dB, which is enough
    to tell "the room got louder" from "someone spoke": the rumble that dominates
    a raw RMS number sits under 80 Hz and does not survive this.
    """
    return _biquad(
        _biquad(samples, _low_pass(rate, 3_000.0)),
        _high_pass(rate, 300.0),
    )


def _low_pass(rate: int, cutoff: float) -> tuple[float, float, float, float, float]:
    """RBJ low pass, Butterworth Q, normalised so a0 is 1."""
    omega = 2 * math.pi * cutoff / rate
    cosine = math.cos(omega)
    alpha = math.sin(omega) / (2 * math.sqrt(0.5))
    a0 = 1 + alpha
    return (
        (1 - cosine) / 2 / a0,
        (1 - cosine) / a0,
        (1 - cosine) / 2 / a0,
        -2 * cosine / a0,
        (1 - alpha) / a0,
    )


def _high_pass(rate: int, cutoff: float) -> tuple[float, float, float, float, float]:
    omega = 2 * math.pi * cutoff / rate
    cosine = math.cos(omega)
    alpha = math.sin(omega) / (2 * math.sqrt(0.5))
    a0 = 1 + alpha
    return (
        (1 + cosine) / 2 / a0,
        -(1 + cosine) / a0,
        (1 + cosine) / 2 / a0,
        -2 * cosine / a0,
        (1 - alpha) / a0,
    )


def _biquad(
    samples: list[float], coefficients: tuple[float, float, float, float, float]
) -> list[float]:
    """Direct form I; the state is two past inputs and two past outputs."""
    b0, b1, b2, a1, a2 = coefficients
    output = [0.0] * len(samples)
    x1 = x2 = y1 = y2 = 0.0
    for index, value in enumerate(samples):
        result = b0 * value + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, value
        y2, y1 = y1, result
        output[index] = result
    return output


def band_shares(samples: array, rate: int) -> dict[str, float]:
    """Share of the signal's power in four bands, from averaged spectra.

    Pure-Python radix-2 FFT: no numpy in this environment, and a few hundred
    milliseconds of arithmetic for ten seconds of audio is acceptable here.
    """
    size = 2048
    if len(samples) < size:
        return {}
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (size - 1)) for i in range(size)]
    bands = {
        "dc_and_rumble": (0.0, 80.0),
        "low": (80.0, 300.0),
        "speech": (300.0, 3_000.0),
        "high": (3_000.0, 8_000.0),
    }
    totals = {name: 0.0 for name in bands}
    overall = 0.0
    for start in range(0, len(samples) - size + 1, size):
        block = samples[start : start + size]
        spectrum = _fft([block[i] * window[i] for i in range(size)])
        for index in range(size // 2 + 1):
            frequency = index * rate / size
            energy = (spectrum[index].real**2 + spectrum[index].imag**2)
            overall += energy
            for name, (low, high) in bands.items():
                if low <= frequency < high:
                    totals[name] += energy
                    break
    if overall <= 0:
        return {name: 0.0 for name in bands}
    return {name: value / overall for name, value in totals.items()}


def _fft(values: list[float]) -> list[complex]:
    """Iterative radix-2 FFT; length must be a power of two."""
    count = len(values)
    data = [complex(value, 0.0) for value in values]
    target = 0
    for index in range(1, count):
        bit = count >> 1
        while target & bit:
            target ^= bit
            bit >>= 1
        target |= bit
        if index < target:
            data[index], data[target] = data[target], data[index]
    length = 2
    while length <= count:
        angle = -2 * math.pi / length
        step = complex(math.cos(angle), math.sin(angle))
        for start in range(0, count, length):
            weight = complex(1.0, 0.0)
            for offset in range(length // 2):
                even = data[start + offset]
                odd = data[start + offset + length // 2] * weight
                data[start + offset] = even + odd
                data[start + offset + length // 2] = even - odd
                weight *= step
        length <<= 1
    return data


def capture(
    source: str, seconds: float, play: Path | None = None
) -> tuple[array, list[str]]:
    """Record `seconds` from `source`, optionally playing a file into the room."""
    notes: list[str] = []
    required = int(seconds * RATE * 2)  # s16le, mono
    command = [
        "parec",
        f"--device={source}",
        "--format=s16le",
        f"--rate={RATE}",
        "--channels=1",
        "--latency-msec=50",
        "--raw",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    playback: subprocess.Popen | None = None
    chunks: list[bytes] = []
    try:
        if play is not None:
            time.sleep(0.3)
            playback = subprocess.Popen(
                ["paplay", str(play)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            notes.append(f"played {play.name} during the recording")
        assert process.stdout is not None
        read = 0
        while read < required:
            chunk = process.stdout.read(min(65_536, required - read))
            if not chunk:
                break
            chunks.append(chunk)
            read += len(chunk)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        if playback is not None:
            try:
                playback.wait(timeout=30)
            except subprocess.TimeoutExpired:
                playback.kill()

    if process.returncode not in (0, -15, 143) and not chunks:
        stderr = b""
        if process.stderr is not None:
            stderr = process.stderr.read()
        raise SystemExit(f"parec failed: {stderr.decode(errors='replace').strip()}")

    raw = b"".join(chunks)
    pcm = array("h")
    pcm.frombytes(raw[: len(raw) - len(raw) % 2])
    return array("f", (value / 32_768.0 for value in pcm)), notes


def write_wav(path: Path, samples: array) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(
            array(
                "h",
                (max(-32768, min(32767, round(value * 32767))) for value in samples),
            ).tobytes()
        )


def default_source() -> str:
    result = subprocess.run(
        ["pactl", "get-default-source"], capture_output=True, text=True
    )
    name = result.stdout.strip()
    if result.returncode != 0 or not name:
        raise SystemExit(
            "no default capture source; is PipeWire/PulseAudio running? "
            f"({result.stderr.strip()})"
        )
    return name


def source_details(name: str) -> list[str]:
    """The lines a reader needs to act: rate, state, gain, active port."""
    listing = subprocess.run(
        ["pactl", "list", "sources"], capture_output=True, text=True
    ).stdout
    block = ""
    for candidate in listing.split("\n\n"):
        if name in candidate:
            block = candidate
            break
    if not block:
        return []
    facts: list[str] = []
    match = re.search(r"(s\d+le|float32le)\s+(\d+)ch\s+(\d+)Hz", block)
    if match:
        facts.append(f"{match.group(1)} {match.group(2)}ch {match.group(3)} Hz")
    match = re.search(r"(?:状态|State)[:：]\s*(\S+)", block)
    if match:
        facts.append(f"state {match.group(1).lower()}")
    match = re.search(r"(\d+)%\s*/\s*([-+0-9.]+)\s*dB", block)
    if match:
        facts.append(f"volume {match.group(1)}% ({match.group(2)} dB)")
    match = re.search(r"(?:活动端口|Active Port)[:：]\s*(\S+)", block)
    if match:
        facts.append(f"active port {match.group(1)}")
    match = re.search(r'alsa\.card = "(\d+)"', block)
    if match:
        facts.append(f"alsa card {match.group(1)} (alsamixer -c {match.group(1)})")
    return facts


def report(title: str, measurement: Measurement) -> None:
    print(f"{title} ({measurement.seconds:.1f} s)")
    print(
        f"  rms {measurement.rms_dbfs:6.1f} dBFS   "
        f"peak {measurement.peak_dbfs:6.1f} dBFS   "
        f"dc {measurement.dc_dbfs:6.1f} dBFS   "
        f"clipped {measurement.clipped_share * 100:.2f}%"
    )
    print(
        f"  20 ms frames: median {measurement.median_dbfs:6.1f}   "
        f"p90 {measurement.loud_dbfs:6.1f}   max {measurement.max_dbfs:6.1f}   "
        f"above {SPEECH_FLOOR_DBFS:.0f} dBFS "
        f"{measurement.above_floor_share * 100:.0f}%"
    )
    print(
        f"  300-3000 Hz band: median {measurement.speech_median_dbfs:6.1f}   "
        f"p90 {measurement.speech_loud_dbfs:6.1f}"
    )
    if measurement.bands:
        shares = "   ".join(
            f"{name} {share * 100:.0f}%" for name, share in measurement.bands.items()
        )
        print(f"  power by band: {shares}")


def checks_for(
    quiet: Measurement, spoken: Measurement | None, played: bool
) -> list[Check]:
    checks: list[Check] = []
    if quiet.rms_dbfs <= MUTED_RMS_DBFS:
        checks.append(
            Check(
                "fail",
                f"the input is effectively silent (rms {quiet.rms_dbfs:.1f} dBFS); "
                "check the mute switch and the capture volume",
            )
        )
    else:
        checks.append(
            Check("ok", f"the input delivers audio (rms {quiet.rms_dbfs:.1f} dBFS)")
        )

    gap = quiet.loud_dbfs - quiet.speech_loud_dbfs
    if gap >= SILENT_BAND_GAP_DB:
        checks.append(
            Check(
                "warn",
                f"the speech band is {gap:.0f} dB below the broadband level: this "
                "input carries rumble or a DC offset and no audible room. At the "
                "same time a hot capture chain (mic boost plus capture gain) makes "
                "an open input look loud, so check what is plugged into the jack "
                "before raising anything: `alsamixer` shows the boost",
            )
        )
    if quiet.dc_dbfs > DC_WARN_DBFS:
        checks.append(
            Check(
                "warn",
                f"DC offset {quiet.dc_dbfs:.1f} dBFS; the analog stage is settling "
                "or the input is biased. It eats headroom and no ASR can use it",
            )
        )
    if quiet.clipped_share > CLIP_SHARE_WARN:
        checks.append(
            Check(
                "warn",
                f"{quiet.clipped_share * 100:.2f}% of the quiet phase is clipped; "
                "lower the capture gain",
            )
        )

    if spoken is None:
        return checks
    # Measured in the speech band, not broadband: a room's rumble fluctuates by
    # several dB without anyone speaking, and a broadband gate would call that a
    # voice. The vendor's ASR only cares about 300-3000 Hz, so this is the number
    # that decides whether a voice turn can work.
    snr = spoken.speech_loud_dbfs - quiet.speech_median_dbfs
    if snr < 3.0:
        checks.append(
            Check(
                "fail",
                f"nothing rose in the speech band (p90 {spoken.speech_loud_dbfs:.1f} "
                f"vs quiet median {quiet.speech_median_dbfs:.1f} dBFS). "
                + (
                    "The file was played, so either the speakers are silent or the "
                    "microphone does not hear the room"
                    if played
                    else "Run it again and speak during the spoken phase"
                ),
            )
        )
    elif snr < MIN_SPEECH_SNR_DB:
        checks.append(
            Check(
                "fail",
                f"speech is only {snr:.1f} dB above the noise in the speech band; an "
                f"ASR needs about {MIN_SPEECH_SNR_DB:.0f} dB. Move closer, lower the "
                "capture gain or treat the room",
            )
        )
    else:
        checks.append(
            Check(
                "ok",
                f"speech is {snr:.1f} dB above the noise in the 300-3000 Hz band",
            )
        )
    if spoken.clipped_share > CLIP_SHARE_WARN:
        checks.append(
            Check(
                "warn",
                f"{spoken.clipped_share * 100:.2f}% of the spoken phase is clipped; "
                "the input is too hot for a voice at this distance",
            )
        )
    return checks


def self_test() -> int:
    """Prove the measurement before trusting it on a device.

    The band pass decides pass or fail, so it is exercised on signals whose
    answer is known: a 1 kHz tone must survive it, a 50 Hz rumble must not, and a
    -20 dBFS sine must read -20 dBFS.
    """
    failures = 0

    def check(name: str, passed: bool, detail: str) -> None:
        nonlocal failures
        failures += 0 if passed else 1
        print(f"{'ok  ' if passed else 'FAIL'} {name}: {detail}")

    def sine(hz: float, amplitude: float) -> array:
        return array(
            "f",
            (
                amplitude * math.sin(2 * math.pi * hz * index / RATE)
                for index in range(RATE)
            ),
        )

    tone = analyse(sine(1_000.0, 0.1))
    # A sine of amplitude 0.1 peaks at -20 dBFS and, because RMS is 3 dB below
    # peak, must read about -23 dBFS. This pins the absolute scale: every number
    # this script prints is an RMS level, not a peak.
    check(
        "level calibration",
        abs(tone.rms_dbfs + 23.01) <= 1.0,
        f"a 1 kHz sine peaking at -20 dBFS reads rms {tone.rms_dbfs:.1f} dBFS",
    )
    check(
        "a voice-frequency tone survives the band pass",
        abs(tone.speech_loud_dbfs - tone.loud_dbfs) <= 3.0,
        f"1 kHz reads {tone.speech_loud_dbfs:.1f} dBFS in the speech band, "
        f"{tone.loud_dbfs:.1f} dBFS broadband",
    )
    rumble = analyse(sine(50.0, 0.1))
    check(
        "rumble does not",
        rumble.speech_loud_dbfs <= rumble.loud_dbfs - 12.0,
        f"50 Hz reads {rumble.speech_loud_dbfs:.1f} dBFS in the speech band, "
        f"{rumble.loud_dbfs:.1f} dBFS broadband",
    )
    offset = analyse(array("f", [0.5] * RATE))
    check(
        "a DC offset is reported",
        offset.dc_dbfs >= -7.0,
        f"a 0.5 constant reads dc {offset.dc_dbfs:.1f} dBFS",
    )
    print(f"{failures} failures")
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source",
        default=None,
        help="capture source name (default: the server's default source)",
    )
    parser.add_argument(
        "--quiet",
        type=float,
        default=3.0,
        help="seconds recorded in silence first (default: 3)",
    )
    parser.add_argument(
        "--speech",
        type=float,
        default=6.0,
        help="seconds recorded for the spoken phase (default: 6)",
    )
    parser.add_argument(
        "--play",
        type=Path,
        default=None,
        help="play this file through the default output during the spoken phase "
        "instead of asking a person to speak (tests speakers and microphone)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="directory for the two WAVs (default: .cache/audio-check)",
    )
    parser.add_argument(
        "--json", action="store_true", help="print one JSON object instead of a report"
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the measurement itself on synthetic signals and exit",
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    if shutil.which("parec") is None:
        raise SystemExit("parec is not installed; it ships with pipewire/pulseaudio")
    if args.play is not None and not args.play.is_file():
        raise SystemExit(f"no such file to play: {args.play}")
    if args.play is not None and shutil.which("paplay") is None:
        raise SystemExit("--play needs paplay")

    source = args.source or default_source()
    details = source_details(source)
    default = args.source is None
    if not args.json:
        print(f"capture source: {source}{' (default)' if default else ''}")
        if details:
            print(f"  {', '.join(details)}")

    quiet_samples, _ = capture(source, args.quiet)
    quiet = analyse(quiet_samples)
    if not args.json:
        report("quiet phase", quiet)

    spoken: Measurement | None = None
    spoken_samples: array | None = None
    notes: list[str] = []
    # Prompts go to stderr so `--json` stays one parseable object on stdout, and
    # stdout is flushed first so the prompt lands after the report when piped.
    sys.stdout.flush()
    if args.play is None:
        print(
            f"Speak now, in your normal voice and at your normal distance, "
            f"for {args.speech:.0f} s...",
            file=sys.stderr,
        )
    else:
        print(f"Playing {args.play.name} for {args.speech:.0f} s...", file=sys.stderr)
    spoken_samples, notes = capture(source, args.speech, args.play)
    if spoken_samples:
        spoken = analyse(spoken_samples)
        if not args.json:
            if notes:
                print(f"  ({notes[0]})")
            report("spoken phase", spoken)

    checks = checks_for(quiet, spoken, played=args.play is not None)

    out_dir = args.out or Path(__file__).resolve().parents[1] / ".cache/audio-check"
    quiet_path = out_dir / "quiet.wav"
    write_wav(quiet_path, quiet_samples)
    spoken_path = out_dir / "spoken.wav"
    if spoken_samples:
        write_wav(spoken_path, spoken_samples)

    if args.json:
        print(
            json.dumps(
                {
                    "source": source,
                    "details": details,
                    "quiet": quiet.as_json(),
                    "spoken": spoken.as_json() if spoken else None,
                    "checks": [
                        {"status": c.status, "message": c.message} for c in checks
                    ],
                },
                ensure_ascii=False,
            )
        )
    else:
        print()
        for check in checks:
            print(f"{check.status:4s} {check.message}")
        print(f"\nwrote {quiet_path}")
        if spoken_samples:
            print(f"wrote {spoken_path}")
        print(
            f"listen: paplay {spoken_path if spoken_samples else quiet_path}"
            "   (compare against what you know you said)"
        )

    return 1 if any(check.status == "fail" for check in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
