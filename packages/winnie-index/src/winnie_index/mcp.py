"""MCP server: tool definitions and JSON-RPC dispatch.

The transport (stdio lines or HTTP POST bodies) is handled by the caller; this
module only turns a JSON-RPC request into a response, so both transports share
one implementation. Requests are stateless: no session id, no server-initiated
stream.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from winnie_index.config import Config
from winnie_index.search import get_document, search, status

SERVER_NAME = "local-knowledge"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-06-18"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_documents",
        "description": (
            "Search the owner's local text and Markdown files. Use it when a "
            "question depends on what the owner wrote, decided or saved before. "
            "Returns ranked hits with path, heading, line range and mtime; an "
            "empty list means the corpus has nothing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words or phrases to find."},
                "roots": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict to these root names.",
                },
                "since": {
                    "type": "string",
                    "description": "Only files modified after 7d, 24h, 2w or an ISO date.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_document",
        "description": (
            "Read a document by path. The file is checked against the index "
            "before the text is returned, so the result is never stale. Cite "
            "the path and line numbers you read."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Root-relative path, or an absolute path under a root."},
                "from_line": {"type": "integer", "minimum": 1},
                "lines": {"type": "integer", "minimum": 1, "maximum": 2000},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "index_status",
        "description": (
            "Report index coverage, the last sweep time, and any failed or "
            "sealed documents. Use it when a search seems to be missing files."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class ToolError(RuntimeError):
    """A tool call that cannot be completed for a reportable reason."""


def _text(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": payload}], "isError": False}


def call_tool(conn: sqlite3.Connection, config: Config, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "search_documents":
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ToolError("query is required")
        rows = search(
            conn,
            config,
            query,
            roots=list(arguments.get("roots") or []) or None,
            since=arguments.get("since"),
            limit=arguments.get("limit"),
        )
        if not rows:
            return _text("No indexed document matched that query.")
        lines = []
        for index, hit in enumerate(rows, start=1):
            heading = f" — {hit['heading']}" if hit["heading"] else ""
            lines.append(
                f"{index}. {hit['root']}:{hit['path']}:{hit['start_line']}-{hit['end_line']}"
                f"{heading} (modified {hit['mtime']})"
            )
            lines.append(f"   {hit['snippet']}")
        return _text("\n".join(lines))

    if name == "get_document":
        path = str(arguments.get("path") or "").strip()
        if not path:
            raise ToolError("path is required")
        from_line = int(arguments.get("from_line") or 1)
        lines = arguments.get("lines")
        document = get_document(
            conn,
            config,
            path,
            from_line=from_line,
            lines=int(lines) if lines else None,
        )
        header = (
            f"{document['root']}:{document['path']} "
            f"lines {document['from_line']}-"
            f"{document['from_line'] + document['text'].count(chr(10))}"
            f" of {document['total_lines']}"
        )
        return _text(header + "\n\n" + str(document["text"]))

    if name == "index_status":
        data = status(conn, config)
        return _text(json.dumps(data, ensure_ascii=False, indent=2))

    raise ToolError(f"unknown tool: {name}")


def handle(
    conn: sqlite3.Connection, config: Config, request: dict[str, Any]
) -> dict[str, Any] | None:
    """Handle one JSON-RPC message; return None for notifications."""
    method = request.get("method")
    request_id = request.get("id")
    is_notification = "id" not in request

    def result(value: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": value}

    def error(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        params = request.get("params") or {}
        requested = params.get("protocolVersion")
        version = requested if isinstance(requested, str) else PROTOCOL_VERSION
        response = result(
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        )
        return None if is_notification else response

    if method in ("notifications/initialized", "notifications/cancelled", "initialized"):
        return None

    if method == "ping":
        return None if is_notification else result({})

    if method == "tools/list":
        return None if is_notification else result({"tools": TOOLS})

    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return error(-32602, "tools/call needs a tool name")
        if not isinstance(arguments, dict):
            return error(-32602, "arguments must be an object")
        try:
            payload = call_tool(conn, config, name, arguments)
        except ToolError as exc:
            payload = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except LookupError as exc:
            payload = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except ValueError as exc:
            payload = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        return None if is_notification else result(payload)

    return error(-32601, f"method not found: {method}")
