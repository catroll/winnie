"""Minute policy: a minute without change must leave nothing behind."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from winnie_senses.roomd.config import Config
from winnie_senses.roomd.service import RoomService

from . import jpeg_bytes

STEP = 1.0 / 3.0 + 0.001


class MinutePolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.service = RoomService(
            Config(out_dir=self.root, target_fps=3.0, change_threshold=3.0)
        )
        self.t0 = dt.datetime(2026, 11, 7, 16, 0, 0).timestamp()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def feed(self, start: int, count: int, jpeg: bytes) -> None:
        for index in range(start, start + count):
            self.service._on_frame(self.t0 + index * STEP, jpeg)

    def test_still_minute_is_counted_static_and_keeps_no_segment(self) -> None:
        still = jpeg_bytes(128)
        self.feed(0, 200, still)  # 0..66 s, crosses one minute boundary
        state = self.service.state()
        self.assertGreaterEqual(state["minutes_static"], 1)
        self.assertEqual(state["minutes_saved"], 0)
        self.assertIsNone(self.service._segment)
        self.assertFalse((self.root / "segments").exists())

    def test_change_opens_a_segment_for_that_minute(self) -> None:
        still = jpeg_bytes(60)
        self.feed(0, 30, still)  # baseline only; no event yet
        self.assertIsNone(self.service._segment)

        self.feed(30, 1, jpeg_bytes(230))  # a body-sized change
        self.assertIsNotNone(self.service._segment)
        assert self.service._segment is not None
        self.assertEqual(len(self.service._segment.frames), 1)

    def test_first_frame_is_a_baseline_not_an_event(self) -> None:
        self.feed(0, 1, jpeg_bytes(128))
        self.assertIsNone(self.service._segment)
        self.assertEqual(self.service.state()["frames_kept"], 0)


if __name__ == "__main__":
    unittest.main()
