"""Minute segments: static minutes vanish, changed minutes become one file."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from winnie_senses.roomd.segment import Frame, MinuteSegment, minute_start, read_index

from . import jpeg_bytes

HAS_FFMPEG = shutil.which("ffmpeg") is not None


class MinuteBoundaryTest(unittest.TestCase):
    def test_timestamp_maps_to_its_minute(self) -> None:
        import datetime as dt

        moment = dt.datetime(2026, 11, 7, 16, 25, 41)
        start = minute_start(moment.timestamp())
        self.assertEqual(start, dt.datetime(2026, 11, 7, 16, 25, 0))

    def test_two_timestamps_in_one_minute_share_a_start(self) -> None:
        import datetime as dt

        a = dt.datetime(2026, 11, 7, 16, 25, 1).timestamp()
        b = dt.datetime(2026, 11, 7, 16, 25, 59).timestamp()
        self.assertEqual(minute_start(a), minute_start(b))


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg is required to mux a segment")
class SegmentMuxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_minute_writes_nothing(self) -> None:
        import datetime as dt

        segment = MinuteSegment(dt.datetime(2026, 11, 7, 16, 25), self.root)
        self.assertIsNone(segment.finalize())
        self.assertFalse((self.root / "segments").exists())

    def test_changed_minute_becomes_a_video(self) -> None:
        import datetime as dt
        import time

        start = dt.datetime(2026, 11, 7, 16, 25)
        base = start.timestamp() + 5
        segment = MinuteSegment(start, self.root)
        for index, offset in enumerate((0.0, 0.5, 9.0)):
            segment.add(Frame(base + offset, jpeg_bytes(20 + index * 90), 5.0 + index))
        entry = segment.finalize()
        self.assertIsNotNone(entry)
        path = self.root / entry["file"]
        self.assertTrue(path.exists())
        self.assertEqual(entry["frames"], 3)
        self.assertEqual(entry["start"], start.isoformat())
        self.assertGreater(path.stat().st_size, 0)

        # Variable frame rate: the third frame is 9 s after the first, not 0.67 s.
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
        )
        stamps = [float(line.strip(",")) for line in probe.stdout.splitlines() if line.strip(",")]
        self.assertEqual(len(stamps), 3)
        self.assertGreater(stamps[-1] - stamps[0], 8.0)

    def test_relative_root_muxes_without_double_prefix(self) -> None:
        import datetime as dt
        import os

        previous = os.getcwd()
        try:
            os.chdir(self.root)  # a relative --out-dir must still work
            segment = MinuteSegment(dt.datetime(2026, 11, 7, 16, 30), Path("cam"))
            segment.add(Frame(dt.datetime(2026, 11, 7, 16, 30, 5).timestamp(), jpeg_bytes(90), 5.0))
            entry = segment.finalize()
        finally:
            os.chdir(previous)
        self.assertIsNotNone(entry)
        self.assertTrue((self.root / "cam" / entry["file"]).exists())

    def test_second_segment_in_the_same_minute_does_not_overwrite(self) -> None:
        import datetime as dt

        start = dt.datetime(2026, 11, 7, 16, 40)
        first = MinuteSegment(start, self.root)
        first.add(Frame(start.timestamp() + 1, jpeg_bytes(10), 5.0))
        first_entry = first.finalize()
        second = MinuteSegment(start, self.root)
        second.add(Frame(start.timestamp() + 30, jpeg_bytes(200), 6.0))
        second_entry = second.finalize()
        assert first_entry is not None and second_entry is not None
        self.assertNotEqual(first_entry["file"], second_entry["file"])
        self.assertEqual(second_entry["file"], "segments/2026-11-07/1640-2.mkv")
        self.assertTrue((self.root / first_entry["file"]).exists())
        self.assertTrue((self.root / second_entry["file"]).exists())

    def test_index_keeps_only_the_tail(self) -> None:
        import datetime as dt

        start = dt.datetime(2026, 11, 7, 16, 25)
        segment = MinuteSegment(start, self.root)
        segment.add(Frame(start.timestamp() + 1, jpeg_bytes(10), 3.0))
        entry = segment.finalize()
        assert entry is not None
        with (self.root / "index.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"file": "other.mkv"}) + "\n")
        tail = read_index(self.root, limit=1)
        self.assertEqual(len(tail), 1)
        self.assertEqual(tail[0]["file"], "other.mkv")


if __name__ == "__main__":
    unittest.main()
