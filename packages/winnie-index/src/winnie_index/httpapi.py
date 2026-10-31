"""HTTP and stdio surfaces.

One daemon serves the REST endpoints, the MCP endpoint, the console's
management endpoints and the background sweep. The stdio mode exists for clients
that cannot reach a port; it talks to the same catalog and uses the same
dispatcher, so the two cannot drift.
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
from winnie_index.admin import AdminError, Service
from winnie_index.config import Config, ConfigError
from winnie_index.search import get_document, search, status

logger = logging.getLogger(__name__)
MAX_BODY = 1_000_000
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
ADMIN_PATHS = ("/admin/config", "/admin/build", "/admin/skill", "/admin/status")


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


def _origin_allowed(origin: str | None) -> bool:
    """Reject browser requests from a non-loopback origin.

    A page on any site can POST to 127.0.0.1; without this check it could
    rewrite the index configuration. Non-browser clients send no Origin header.
    """
    if not origin:
        return True
    try:
        host = urlparse(origin).hostname
    except ValueError:
        return False
    return host in LOOPBACK_HOSTS


class _Handler(BaseHTTPRequestHandler):
    server_version = "winnie-index/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def service(self) -> Service:
        return self.server.service  # type: ignore[attr-defined]

    @property
    def config(self) -> Config:
        return self.service.config

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

    def _guard(self) -> bool:
        if not _origin_allowed(self.headers.get("Origin")):
            self._error(403, "origin not allowed")
            return False
        if not _authorized(self.config, self.headers.get("Authorization")):
            self._error(401, "unauthorized")
            return False
        return True

    # -- GET -------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send(200, {"status": "ok", "version": mcp.SERVER_VERSION})
            return
        if not self._guard():
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
        if parsed.path == "/admin/status":
            self._send(200, self.service.status())
            return
        if parsed.path == "/admin/config":
            self._send(
                200,
                {"path": str(self.service.config_path), "config": self.service.editable()},
            )
            return
        if parsed.path == "/":
            self._send(
                200,
                {
                    "name": mcp.SERVER_NAME,
                    "version": mcp.SERVER_VERSION,
                    "endpoints": ["/search", "/doc", "/status", "/healthz", "/mcp", "/admin"],
                },
            )
            return
        self._error(404, "not found")

    # -- POST / PUT ------------------------------------------------------

    def do_POST(self) -> None:
        self._write_request()

    def do_PUT(self) -> None:
        self._write_request()

    def _write_request(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/search", "/mcp") + ADMIN_PATHS:
            self._error(404, "not found")
            return
        if not self._guard():
            return
        if parsed.path == "/admin/config" and self.command != "PUT":
            self._error(405, "use PUT for /admin/config")
            return
        try:
            body = self._body()
        except ValueError as exc:
            self._error(400, str(exc))
            return

        if parsed.path == "/admin/config":
            payload = body.get("config", body)
            try:
                config = self.service.update_config(payload)
            except ConfigError as exc:
                self._error(400, str(exc))
                return
            except AdminError as exc:
                self._error(409, str(exc))
                return
            self._send(
                200,
                {
                    "config": self.service.editable(),
                    "roots": [root.name for root in config.roots],
                },
            )
            return

        if parsed.path == "/admin/build":
            try:
                result = self.service.rebuild(dry_run=bool(body.get("dry_run", False)))
            except Exception as exc:  # noqa: BLE001 - report, do not crash the daemon
                logger.exception("manual build failed")
                self._error(500, f"build failed: {type(exc).__name__}: {exc}")
                return
            self._send(200, {"build": result})
            return

        if parsed.path == "/admin/skill":
            action = str(body.get("action") or "check")
            if action == "install":
                self._send(200, self.service.install_skill())
            elif action == "check":
                self._send(200, self.service.check_skill())
            else:
                self._error(400, "action must be install or check")
            return

        if parsed.path == "/search":
            query = str(body.get("query") or "").strip()
            if not query:
                self._error(400, "query is required")
                return
            conn = _connect(self.config)
            try:
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
            finally:
                conn.close()
            return

        conn = _connect(self.config)
        try:
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

    def __init__(self, address: tuple[str, int], service: Service) -> None:
        super().__init__(address, _Handler)
        self.service = service


def create_server(
    service: Service | Config, *, host: str | None = None, port: int | None = None
) -> Server:
    if isinstance(service, Config):
        service = Service.from_config(service)
    config = service.config
    return Server(
        (host or config.http_host, port if port is not None else config.http_port),
        service,
    )


def _sweep_loop(service: Service, stop: threading.Event) -> None:
    while not stop.wait(service.config.scan_interval):
        try:
            stats = service.rebuild()
            logger.info("sweep %s", json.dumps(stats))
        except Exception:  # a failed sweep must not kill the daemon
            logger.exception("sweep failed")


def serve(config: Config) -> None:
    """Run a sweep at start, then on a timer, and serve HTTP until interrupted."""
    service = Service.from_config(config)
    try:
        logger.info("initial sweep %s", json.dumps(service.rebuild()))
    except Exception:
        logger.exception("initial sweep failed")

    stop = threading.Event()
    thread = threading.Thread(target=_sweep_loop, args=(service, stop), daemon=True)
    thread.start()

    server = create_server(service)
    logger.info("serving on http://%s:%s", service.config.http_host, service.config.http_port)
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
                print(
                    json.dumps(
                        {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
                    ),
                    flush=True,
                )
                continue
            response = mcp.handle(conn, config, request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        conn.close()
