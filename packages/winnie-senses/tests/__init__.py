"""Tests for the room capture daemon."""

from __future__ import annotations

from io import BytesIO

from PIL import Image


def jpeg_bytes(color: int = 128, size: tuple[int, int] = (64, 48)) -> bytes:
    """A real (tiny) JPEG, because the splitter never invents frame bytes."""
    image = Image.new("L", size, color=color)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
