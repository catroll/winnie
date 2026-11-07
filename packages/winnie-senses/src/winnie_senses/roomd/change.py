"""Cheap frame-to-frame change score.

A 640x480 JPEG decodes in a few milliseconds, but comparing full frames is
wasteful: the score only needs to answer "did anything move".  Each frame is
reduced to a 160x120 grayscale signature and the mean absolute difference of
two signatures is a number in 0..255.  On static scenes the JPEG noise floor
measures ~0.5; a person walking through the frame lands well above 5.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageChops, ImageStat

SIGNATURE_SIZE = (160, 120)


def signature(jpeg: bytes, size: tuple[int, int] = SIGNATURE_SIZE) -> Image.Image:
    """Downscaled grayscale signature of one JPEG frame."""
    with Image.open(BytesIO(jpeg)) as image:
        return image.convert("L").resize(size, Image.BILINEAR)


def score(previous: Image.Image, current: Image.Image) -> float:
    """Mean absolute difference of two signatures, 0..255."""
    diff = ImageChops.difference(previous, current)
    return float(ImageStat.Stat(diff).mean[0])
