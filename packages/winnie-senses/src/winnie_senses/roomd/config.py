"""Settings for the room capture daemon."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    device: str = "/dev/video0"
    width: int = 640
    height: int = 480
    # Frames considered per second; the device itself may run faster.
    target_fps: float = 3.0
    # Mean absolute difference (0..255) at which a frame counts as a change.
    # A still room measures ~0.5 with no motion and ~1.6 with a seated
    # person shifting; walking scores 10-30.  3.0 sits above the quiet
    # band and below real movement.
    change_threshold: float = 3.0
    out_dir: Path = Path("data/camera")
    bind: str = "127.0.0.1"
    port: int = 8077
    retry_seconds: float = 2.0

    def as_dict(self) -> dict:
        return {
            "device": self.device,
            "width": self.width,
            "height": self.height,
            "target_fps": self.target_fps,
            "change_threshold": self.change_threshold,
            "out_dir": str(self.out_dir),
            "bind": self.bind,
            "port": self.port,
        }
