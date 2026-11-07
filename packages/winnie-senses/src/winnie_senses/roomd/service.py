"""The capture loop: own the camera, score frames, cut one file per minute.

The HTTP layer talks to this class; nothing else touches the device.  Muxing
runs on its own thread so a slow encode at a minute boundary never stalls
capture, and a failed encode parks its frames instead of losing them.
"""

from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
from datetime import datetime

from . import change
from .config import Config
from .frames import FfmpegCapture
from .segment import Frame, MinuteSegment, minute_start, read_index

log = logging.getLogger("winnie_senses.roomd")


class RoomService:
    def __init__(self, config: Config) -> None:
        self.cfg = config
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._release_requested = False
        self._paused_until: float | None = None

        self._capture_thread: threading.Thread | None = None
        self._mux_thread: threading.Thread | None = None
        self._mux_queue: queue.Queue[MinuteSegment | None] = queue.Queue()

        self._latest_jpeg: bytes | None = None
        self._segment: MinuteSegment | None = None
        self._minute: datetime | None = None
        self._last_signature = None
        self._last_eval = 0.0
        self._started_at = time.time()

        self._state: dict = {
            "camera": "starting",
            "device": config.device,
            "resolution": f"{config.width}x{config.height}",
            "target_fps": config.target_fps,
            "change_threshold": config.change_threshold,
            "frames_seen": 0,
            "frames_scored": 0,
            "frames_kept": 0,
            "last_score": None,
            "last_frame_at": None,
            "minutes_static": 0,
            "minutes_saved": 0,
            "minutes_failed": 0,
            "last_saved": None,
            "last_error": None,
            "paused_until": None,
            "started_at": datetime.fromtimestamp(self._started_at).isoformat(),
        }

    # ---- lifecycle -----------------------------------------------------

    def start(self) -> None:
        self.cfg.out_dir.mkdir(parents=True, exist_ok=True)
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="roomd-capture", daemon=True
        )
        self._capture_thread.start()
        self._mux_thread = threading.Thread(
            target=self._mux_loop, name="roomd-mux", daemon=True
        )
        self._mux_thread.start()
        log.info(
            "capturing %s at %dx%d, %s fps, threshold %.1f, out %s",
            self.cfg.device,
            self.cfg.width,
            self.cfg.height,
            self.cfg.target_fps,
            self.cfg.change_threshold,
            self.cfg.out_dir,
        )

    def stop(self) -> None:
        self._stop.set()
        self._release_requested = True
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=5)
            self._capture_thread = None
        self._close_minute()
        self._mux_queue.put(None)
        if self._mux_thread is not None:
            self._mux_thread.join(timeout=30)
            self._mux_thread = None
        self._set(camera="stopped")
        log.info("stopped")

    # ---- control -------------------------------------------------------

    def release(self, seconds: float | None = None) -> dict:
        """Let go of the device so another application can use the camera."""
        with self._lock:
            self._paused_until = (
                time.time() + seconds if seconds else float("inf")
            )
            until = (
                None
                if seconds is None
                else datetime.fromtimestamp(self._paused_until).isoformat()
            )
            self._state["paused_until"] = until
        self._release_requested = True
        deadline = time.time() + 3.0
        while time.time() < deadline and self.state().get("camera") != "paused":
            time.sleep(0.05)
        log.info("camera released (seconds=%s)", seconds)
        return self.state()

    def resume(self) -> dict:
        with self._lock:
            self._paused_until = None
            self._state["paused_until"] = None
        self._release_requested = False
        self._set(camera="starting")
        log.info("camera resumed")
        return self.state()

    # ---- reads ---------------------------------------------------------

    def state(self) -> dict:
        with self._lock:
            data = dict(self._state)
        segment = self._segment
        data["segment_frames"] = len(segment.frames) if segment is not None else 0
        data["segment_minute"] = segment.start.isoformat() if segment is not None else None
        try:
            data["disk_free_bytes"] = shutil.disk_usage(self.cfg.out_dir).free
        except OSError:
            data["disk_free_bytes"] = None
        return data

    def snapshot(self) -> bytes | None:
        return self._latest_jpeg

    def segments(self, limit: int = 50) -> list[dict]:
        return read_index(self.cfg.out_dir, limit)

    # ---- capture loop --------------------------------------------------

    def _capture_loop(self) -> None:
        while not self._stop.is_set():
            remaining = self._pause_remaining()
            if remaining is not None:
                if remaining <= 0:
                    self.resume()
                    continue
                self._set(camera="paused")
                self._stop.wait(min(0.2, remaining))
                continue
            self._capture_once()

    def _pause_remaining(self) -> float | None:
        with self._lock:
            if self._paused_until is None:
                return None
            return self._paused_until - time.time()

    def _capture_once(self) -> None:
        capture = FfmpegCapture(self.cfg.device, self.cfg.width, self.cfg.height)
        try:
            capture.start()
        except OSError as exc:
            self._set(camera="error", last_error=f"cannot start capture: {exc}")
            self._stop.wait(self.cfg.retry_seconds)
            return
        self._set(camera="running", last_error=None)
        try:
            for ts, jpeg in capture.frames():
                if self._stop.is_set() or self._release_requested:
                    break
                self._on_frame(ts, jpeg)
        except Exception as exc:  # a broken pipe must not kill the daemon
            self._set(last_error=f"capture stream: {exc}")
        finally:
            capture.stop()

        if self._release_requested:
            self._release_requested = False
            self._set(camera="paused")
            return
        if not self._stop.is_set():
            self._set(
                camera="error",
                last_error=capture.stderr_text() or "capture process exited",
            )
            self._stop.wait(self.cfg.retry_seconds)

    def _on_frame(self, ts: float, jpeg: bytes) -> None:
        self._latest_jpeg = jpeg
        self._inc("frames_seen")
        self._set(last_frame_at=datetime.fromtimestamp(ts).isoformat())

        period = 1.0 / self.cfg.target_fps
        if ts - self._last_eval < period:
            return
        self._last_eval = ts
        try:
            signature = change.signature(jpeg)
        except Exception as exc:
            self._set(last_error=f"cannot decode frame: {exc}")
            return

        previous = self._last_signature
        if previous is None:
            # The first frame is a baseline, not an event: a daemon that starts
            # in a still room must not write a file merely for having started.
            self._last_signature = signature
            self._inc("frames_scored")
            self._set(last_score=0.0)
            return
        current = change.score(previous, signature)
        self._inc("frames_scored")
        self._set(last_score=round(current, 2))

        minute = minute_start(ts)
        if self._minute is not None and minute != self._minute:
            self._close_minute()
        self._minute = minute

        if current < self.cfg.change_threshold:
            return
        segment = self._segment
        if segment is None:
            segment = MinuteSegment(minute, self.cfg.out_dir, period)
            self._segment = segment
        segment.add(Frame(ts, jpeg, current))
        self._last_signature = signature
        self._inc("frames_kept")

    def _close_minute(self) -> None:
        segment, self._segment = self._segment, None
        if segment is None or segment.empty:
            self._inc("minutes_static")
            return
        self._mux_queue.put(segment)

    # ---- muxing --------------------------------------------------------

    def _mux_loop(self) -> None:
        while True:
            segment = self._mux_queue.get()
            if segment is None:
                return
            try:
                entry = segment.finalize()
            except Exception as exc:
                self._inc("minutes_failed")
                self._set(last_error=f"mux failed: {exc}")
                log.warning("mux failed for %s: %s", segment.start, exc)
                continue
            if entry is not None:
                self._inc("minutes_saved")
                self._set(last_saved=entry["file"])
                log.info(
                    "saved %s (%d frames, %.1f KB, max score %.1f)",
                    entry["file"],
                    entry["frames"],
                    entry["bytes"] / 1024,
                    entry["max_score"],
                )

    # ---- state helpers -------------------------------------------------

    def _set(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def _inc(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._state[key] = self._state.get(key, 0) + amount
