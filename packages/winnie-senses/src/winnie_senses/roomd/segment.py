"""One video file per wall-clock minute, holding only frames that changed.

A minute in which nothing moved produces no file at all.  Kept frames keep
their real timestamps: the muxer writes a variable-framerate Matroska, so a
minute with one event at second 40 plays as a 1-frame file stamped 40 s in,
not as a 3-second clip of unrelated time.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Frame:
    ts: float  # wall clock, seconds since epoch
    jpeg: bytes
    score: float


def minute_start(ts: float) -> datetime:
    """The wall-clock minute a timestamp belongs to."""
    return datetime.fromtimestamp(ts).replace(second=0, microsecond=0)


def _quote(path: Path) -> str:
    return "'" + str(path).replace("'", r"'\''") + "'"


@dataclass
class MinuteSegment:
    """Frames belonging to one minute, until `finalize` writes the file."""

    start: datetime
    root: Path
    frame_period: float = 1.0 / 3.0
    frames: list[Frame] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.frames

    @property
    def path(self) -> Path:
        day = self.start.strftime("%Y-%m-%d")
        return self.root / "segments" / day / f"{self.start:%H%M}.mkv"

    def add(self, frame: Frame) -> None:
        self.frames.append(frame)

    @staticmethod
    def _free_path(path: Path) -> Path:
        """Never overwrite an earlier file from the same minute.

        A daemon restart inside the current minute finalises a partial segment
        with the same `HHMM.mkv` name; without this the first capture would be
        silently replaced.
        """
        if not path.exists():
            return path
        for counter in range(2, 100):
            candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
            if not candidate.exists():
                return candidate
        return path.with_name(f"{path.stem}-{uuid.uuid4().hex[:4]}{path.suffix}")

    def mux_command(self, list_file: Path, out_file: Path) -> list[str]:
        return [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "26",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(out_file),
        ]

    def finalize(self) -> dict | None:
        """Write the minute to disk; `None` when the minute never changed."""
        if self.empty:
            return None
        out = self._free_path(self.path)
        # Absolute: the concat demuxer resolves entries relative to the
        # list file, so a relative root would be prepended twice.
        tmp = (
            self.root / "tmp" / f"{self.start:%Y%m%d-%H%M}-{uuid.uuid4().hex[:6]}"
        ).resolve()
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            lines: list[str] = []
            for index, frame in enumerate(self.frames):
                name = f"f{index:05d}.jpg"
                (tmp / name).write_bytes(frame.jpeg)
                if index + 1 < len(self.frames):
                    duration = max(0.01, self.frames[index + 1].ts - frame.ts)
                else:
                    duration = self.frame_period
                lines.append(f"file {_quote(tmp / name)}\nduration {duration:.5f}\n")
            list_file = tmp / "list.txt"
            list_file.write_text("".join(lines), encoding="utf-8")

            out.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                self.mux_command(list_file, out.resolve()), capture_output=True, timeout=300
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-500:].strip())

            entry = {
                "file": str(out.relative_to(self.root)),
                "start": self.start.isoformat(),
                "frames": len(self.frames),
                "bytes": out.stat().st_size,
                "first_frame": datetime.fromtimestamp(self.frames[0].ts).isoformat(),
                "last_frame": datetime.fromtimestamp(self.frames[-1].ts).isoformat(),
                "max_score": round(max(f.score for f in self.frames), 2),
            }
            with (self.root / "index.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return entry
        except Exception:
            # Never lose a minute to a mux bug: park the frames where they can
            # be recovered by hand instead of deleting them with the temp dir.
            failed = self.root / "failed"
            failed.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp), str(failed / tmp.name))
            raise
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)


def read_index(root: Path, limit: int = 50) -> list[dict]:
    """The last `limit` entries of the segment index, newest last."""
    index = root / "index.jsonl"
    if not index.exists():
        return []
    lines = index.read_text(encoding="utf-8").splitlines()
    entries: list[dict] = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
