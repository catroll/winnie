#!/usr/bin/env python3
"""Check the shipped wake-word files against the shipped model.

The Android detector cannot run without a phone, but the same sherpa-onnx
release and the same ONNX files run on a desktop. This script answers the two
questions a keyword change has to answer:

1. does a real recording of the wake word trigger the committed keyword file?
2. does ordinary speech trigger it by accident?

Usage:

    python3 scripts/check-wake-keywords.py \
        --model .tmp/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20 \
        --hit path/to/a/real/winnie.wav \
        [--hit another.wav]

It needs `pip install sherpa-onnx` (2.x, the same major as the AAR) and the
model tarball from
https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2

The decoy set is the model's own `test_wavs`, which are real recordings. A
synthetic voice is not usable here: `espeak-ng` fails the release's own
`LIGHT UP` keyword, so a TTS miss says nothing about a human miss.

Exit status is 0 when every `--hit` recording fires and no decoy does, 1
otherwise. That makes it safe to run after editing `keywords.txt`.
"""

from __future__ import annotations

import argparse
import sys
import wave
from array import array
from pathlib import Path

import sherpa_onnx

ASSETS = Path(__file__).resolve().parents[1] / "apps/android/app/src/main/assets/kws"


def read_wav(path: Path) -> tuple[int, array]:
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    raw = array("h")
    raw.frombytes(frames)
    return rate, array("f", (value / 32768.0 for value in raw))


def make_spotter(model_dir: Path) -> sherpa_onnx.KeywordSpotter:
    # These mirror SherpaWakeWordDetector.config(): same assets, same score,
    # same threshold, same trailing-blank count.
    return sherpa_onnx.KeywordSpotter(
        tokens=str(ASSETS / "tokens.txt"),
        encoder=str(ASSETS / "encoder.int8.onnx"),
        decoder=str(ASSETS / "decoder.onnx"),
        joiner=str(ASSETS / "joiner.int8.onnx"),
        keywords_file=str(ASSETS / "keywords.txt"),
        num_threads=2,
        sample_rate=16000,
        feature_dim=80,
        keywords_score=1.5,
        keywords_threshold=0.25,
        num_trailing_blanks=0,
        provider="cpu",
    )


def spot(spotter: sherpa_onnx.KeywordSpotter, path: Path) -> list[str]:
    rate, samples = read_wav(path)
    stream = spotter.create_stream()
    stream.accept_waveform(rate, samples)
    stream.input_finished()
    hits: list[str] = []
    while spotter.is_ready(stream):
        spotter.decode_stream(stream)
        result = spotter.get_result(stream)
        if result:
            hits.append(result)
            spotter.reset_stream(stream)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument(
        "--hit",
        action="append",
        default=[],
        type=Path,
        help="a real recording that must trigger the keyword (repeatable)",
    )
    args = parser.parse_args()

    spotter = make_spotter(args.model)
    failures = 0

    for path in args.hit:
        hits = spot(spotter, path)
        print(f"{'ok  ' if hits else 'FAIL'} hit {path.name}: {hits}")
        failures += 0 if hits else 1

    decoys = sorted((args.model / "test_wavs").glob("*.wav"))
    for path in decoys:
        hits = spot(spotter, path)
        print(f"{'ok  ' if not hits else 'FAIL'} decoy {path.name}: {hits}")
        failures += 1 if hits else 0

    print(f"{len(args.hit)} wake recordings, {len(decoys)} decoys, {failures} failures")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
