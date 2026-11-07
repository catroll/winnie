"""Change score behaviour: quiet on duplicates, loud on real difference."""

from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from winnie_senses.roomd import change

from . import jpeg_bytes


class ChangeScoreTest(unittest.TestCase):
    def test_identical_frames_score_zero(self) -> None:
        frame = jpeg_bytes(128)
        self.assertEqual(change.score(change.signature(frame), change.signature(frame)), 0.0)

    def test_dark_and_bright_score_high(self) -> None:
        dark = change.signature(jpeg_bytes(10))
        bright = change.signature(jpeg_bytes(240))
        self.assertGreater(change.score(dark, bright), 200)

    def test_person_sized_change_is_visible_above_the_noise_floor(self) -> None:
        quiet = Image.new("L", (640, 480), 120)
        moving = quiet.copy()
        # A body at a desk is a large part of the frame; a 40 px blob is not,
        # and the area-weighted score is deliberately blind to it.
        ImageDraw.Draw(moving).rectangle([260, 90, 420, 430], fill=255)
        first = change.signature(_encode(quiet))
        second = change.signature(_encode(moving))
        value = change.score(first, second)
        self.assertGreater(value, 2.0)
        self.assertLess(value, 60.0)


def _encode(image: Image.Image) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
