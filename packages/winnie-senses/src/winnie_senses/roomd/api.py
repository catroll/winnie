"""Loopback HTTP API for the room daemon.

Small on purpose: the daemon is the only process allowed to touch the camera,
so everything else — snapshot, state, segments, release — goes through here.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .service import RoomService


class _Handler(BaseHTTPRequestHandler):
    service: RoomService  # set by `make_server`
    server_version = "winnie-roomd/0.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib name
        return  # per-request noise belongs in the access log, not the daemon log

    # ---- helpers -------------------------------------------------------

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self) -> None:
        self._json({"error": "not found", "path": self.path}, status=404)

    # ---- routes --------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        url = urlparse(self.path)
        if url.path == "/health":
            camera = self.service.state().get("camera")
            self._json({"ok": camera in {"running", "paused"}, "camera": camera})
        elif url.path == "/state":
            self._json(self.service.state())
        elif url.path == "/segments":
            limit = _int_param(url.query, "limit", 50, maximum=500)
            self._json({"segments": self.service.segments(limit)})
        elif url.path == "/snapshot":
            jpeg = self.service.snapshot()
            if jpeg is None:
                self._json({"error": "no frame yet"}, status=503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(jpeg)
        elif url.path == "/":
            self._json(
                {
                    "service": "winnie-roomd",
                    "routes": ["/health", "/state", "/snapshot", "/segments"],
                }
            )
        else:
            self._not_found()

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        url = urlparse(self.path)
        if url.path == "/release":
            seconds = _float_param(url.query, "seconds")
            self._json(self.service.release(seconds))
        elif url.path == "/resume":
            self._json(self.service.resume())
        else:
            self._not_found()


def _float_param(query: str, name: str) -> float | None:
    values = parse_qs(query).get(name)
    if not values:
        return None
    try:
        return float(values[0])
    except ValueError:
        return None


def _int_param(query: str, name: str, default: int, maximum: int) -> int:
    values = parse_qs(query).get(name)
    if not values:
        return default
    try:
        return max(1, min(maximum, int(values[0])))
    except ValueError:
        return default


def make_server(service: RoomService, bind: str, port: int) -> ThreadingHTTPServer:
    handler = type("RoomHandler", (_Handler,), {"service": service})
    return ThreadingHTTPServer((bind, port), handler)
