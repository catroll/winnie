"""Read the camera as MJPEG and split the byte stream into JPEG frames.

The UVC device is exclusive: exactly one process may hold it.  This module is
that process's half — ffmpeg opens the device once and copies the already
compressed MJPEG frames to stdout, so no decoding happens here.  Decoding is
only needed for the frames the change detector actually looks at.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Iterator
from typing import BinaryIO

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


class MjpegSplitter:
    """Incremental splitter for a concatenated MJPEG byte stream."""

    def __init__(self, max_buffer: int = 8 * 1024 * 1024) -> None:
        self._buf = bytearray()
        self._max_buffer = max_buffer

    def feed(self, data: bytes) -> list[bytes]:
        """Append a chunk and return every complete JPEG it completed."""
        self._buf.extend(data)
        frames: list[bytes] = []
        while True:
            start = self._buf.find(SOI)
            if start < 0:
                # A start marker can straddle the chunk boundary: keep one 0xFF.
                if self._buf[-1:] == b"\xff":
                    del self._buf[:-1]
                else:
                    self._buf.clear()
                break
            if start:
                del self._buf[:start]
            end = self._buf.find(EOI, 2)
            if end < 0:
                break
            frames.append(bytes(self._buf[: end + 2]))
            del self._buf[: end + 2]
        if len(self._buf) > self._max_buffer:
            # A corrupt stream must not grow without bound; resynchronise.
            self._buf.clear()
        return frames


def capture_command(device: str, width: int, height: int) -> list[str]:
    """ffmpeg command that copies MJPEG frames from the V4L2 device to stdout."""
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "nobuffer",
        "-f",
        "v4l2",
        "-input_format",
        "mjpeg",
        "-video_size",
        f"{width}x{height}",
        "-i",
        device,
        "-c",
        "copy",
        "-f",
        "mjpeg",
        "-",
    ]


class FfmpegCapture:
    """One ffmpeg process that owns the camera and yields `(ts, jpeg)`."""

    def __init__(self, device: str, width: int, height: int) -> None:
        self.device = device
        self.width = width
        self.height = height
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr: BinaryIO | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self.running:
            return
        # A real file on disk (unlinked) is an unbounded stderr sink, so ffmpeg
        # can never block on a full pipe, and the text is still readable after
        # the process dies — which is exactly when it is needed.
        self._stderr = tempfile.TemporaryFile()
        self._proc = subprocess.Popen(
            capture_command(self.device, self.width, self.height),
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            bufsize=0,
        )

    def frames(self) -> Iterator[tuple[float, bytes]]:
        """Yield `(wall-clock timestamp, jpeg bytes)` until the process exits."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        splitter = MjpegSplitter()
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                return
            for jpeg in splitter.feed(chunk):
                yield time.time(), jpeg

    def stderr_text(self, limit: int = 2000) -> str:
        if self._stderr is None:
            return ""
        try:
            self._stderr.seek(0)
            text = self._stderr.read().decode("utf-8", "replace")
        except OSError:
            return ""
        return text[-limit:].strip()

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
        stderr, self._stderr = self._stderr, None
        if stderr is not None:
            stderr.close()
