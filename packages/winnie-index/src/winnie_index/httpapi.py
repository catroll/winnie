"""HTTP and stdio surfaces.

One daemon serves the REST endpoints, the MCP endpoint and the background
sweep. The stdio mode exists for clients that cannot reach a port; it talks to
the same catalog and uses the same dispatcher, so the two cannot drift.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from winnie_index import db, mcp
from winnie_index.config import Config
from winnie_index.scan import scan
from winnie_index.search import get_document, search, status

logger = logging.getLogger(__name__)
MAX_BODY = 1_000_000


def _connect(config: Config) -> sqlite3.Connection:
    conn = db.connect(config.db_path)
    db.ensure_schema(conn)
    return conn


def _authorized(config: Config, header: str | None) -> bool:
    token = os.environ.get(config.token_env, "")
    if not token:
        return True
    if not header:
        return False
    scheme, _, value = header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip(), token)


class _Handler(BaseHTTPRequestHandler):
    server_version = "winnie-index/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def config(self) -> Config:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s %s", self.address_string(), fmt % args)

    def _send(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str) -> None:
        self._send(code, {"error": message})

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send(200, {"status": "ok", "version": mcp.SERVER_VERSION})
            return
        if not _authorized(self.config, self.headers.get("Authorization")):
            self._error(401, "unauthorized")
            return
        if parsed.path == "/status":
            conn = _connect(self.config)
            try:
                self._send(200, status(conn, self.config))
            finally:
                conn.close()
            return
        if parsed.path == "/doc":
            params = parse_qs(parsed.query)
            path = (params.get("path") or [""])[0]
            if not path:
                self._error(400, "path is required")
                return
            from_line = int((params.get("from") or ["1"])[0])
            lines = params.get("lines")
            try:
                conn = _connect(self.config)
                try:
                    result = get_document(
                        conn,
                        self.config,
                        path,
                        roots=params.get("root"),
                        from_line=from_line,
                        lines=int(lines[0]) if lines else None,
                    )
                finally:
                    conn.close()
            except LookupError as exc:
                self._error(404, str(exc))
                return
            self._send(200, result)
            return
        if parsed.path == "/":
            self._send(
                200,
                {
                    "name": mcp.SERVER_NAME,
                    "version": mcp.SERVER_VERSION,
                    "endpoints": ["/search", "/doc", "/status", "/healthz", "/mcp"],
                },
            )
            return
        self._error(404, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not _authorized(self.config, self.headers.get("Authorization")):
            self._error(401, "unauthorized")
            return
        if parsed.path not in ("/search", "/mcp"):
            self._error(404, "not found")
            return
        try:
            body = self._body()
        except ValueError as exc:
            self._error(400, str(exc))
            return

        conn = _connect(self.config)
        try:
            if parsed.path == "/search":
                query = str(body.get("query") or "").strip()
                if not query:
                    self._error(400, "query is required")
                    return
                try:
                    results = search(
                        conn,
                        self.config,
                        query,
                        roots=list(body.get("roots") or []) or None,
                        since=body.get("since"),
                        limit=body.get("limit"),
                    )
                except ValueError as exc:
                    self._error(400, str(exc))
                    return
                self._send(200, {"query": query, "results": results})
                return

            response = mcp.handle(conn, self.config, body)
            if response is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send(200, response)
        finally:
            conn.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: Config) -> None:
        super().__init__(address, _Handler)
        self.config = config


def create_server(config: Config, *, host: str | None = None, port: int | None = None) -> Server:
    return Server((host or config.http_host, port if port is not None else config.http_port), config)


def _sweep_loop(config: Config, stop: threading.Event) -> None:
    while not stop.wait(config.scan_interval):
        try:
            conn = _connect(config)
            try:
                stats = scan(conn, config)
                logger.info("sweep %s", json.dumps(stats.as_dict()))
            finally:
                conn.close()
        except Exception:  # a failed sweep must not kill the daemon
            logger.exception("sweep failed")


def serve(config: Config) -> None:
    """Run a sweep at start, then on a timer, and serve HTTP until interrupted."""
    conn = _connect(config)
    try:
        stats = scan(conn, config)
        logger.info("initial sweep %s", json.dumps(stats.as_dict()))
    finally:
        conn.close()

    stop = threading.Event()
    thread = threading.Thread(target=_sweep_loop, args=(config, stop), daemon=True)
    thread.start()

    server = create_server(config)
    logger.info("serving on http://%s:%s", config.http_host, config.http_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


def run_stdio(config: Config) -> None:
    """Serve MCP over stdin/stdout, one JSON-RPC message per line."""
    import sys

    conn = _connect(config)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}), flush=True)
                continue
            response = mcp.handle(conn, config, request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        conn.close()
