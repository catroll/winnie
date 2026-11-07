"""The MJPEG splitter must survive arbitrary read() boundaries."""

from __future__ import annotations

import unittest

from winnie_senses.roomd.frames import MjpegSplitter, capture_command

from . import jpeg_bytes

FRAME = jpeg_bytes()


class MjpegSplitterTest(unittest.TestCase):
    def test_one_frame_in_one_chunk(self) -> None:
        self.assertEqual(MjpegSplitter().feed(FRAME), [FRAME])

    def test_byte_at_a_time(self) -> None:
        splitter = MjpegSplitter()
        out: list[bytes] = []
        for index in range(len(FRAME)):
            out.extend(splitter.feed(FRAME[index : index + 1]))
        self.assertEqual(out, [FRAME])

    def test_two_frames_in_one_chunk(self) -> None:
        self.assertEqual(MjpegSplitter().feed(FRAME + FRAME), [FRAME, FRAME])

    def test_garbage_before_start_marker_is_dropped(self) -> None:
        splitter = MjpegSplitter()
        self.assertEqual(splitter.feed(b"garbage" + FRAME), [FRAME])

    def test_partial_frame_is_held_back(self) -> None:
        splitter = MjpegSplitter()
        self.assertEqual(splitter.feed(FRAME[:10]), [])
        self.assertEqual(splitter.feed(FRAME[10:]), [FRAME])

    def test_trailing_ff_survives_a_chunk_boundary(self) -> None:
        splitter = MjpegSplitter()
        # End the chunk on the first byte of a start marker.
        self.assertEqual(splitter.feed(FRAME + b"\xff"), [FRAME])
        self.assertEqual(splitter.feed(b"\xd8" + FRAME[2:]), [FRAME])


class CaptureCommandTest(unittest.TestCase):
    def test_requests_mjpeg_copy_from_v4l2(self) -> None:
        command = capture_command("/dev/video0", 640, 480)
        self.assertIn("v4l2", command)
        self.assertIn("mjpeg", command)
        self.assertIn("640x480", command)
        self.assertIn("copy", command)
        self.assertEqual(command[-1], "-")


if __name__ == "__main__":
    unittest.main()
