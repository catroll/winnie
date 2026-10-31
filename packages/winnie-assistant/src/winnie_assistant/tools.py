"""Tools the assistant may call during a turn.

Every tool returns plain text that is safe to put in the model context: no
exceptions escape, no response is unbounded, and no tool has a side effect
outside the memory directory.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import logging
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from winnie_assistant.memory import MemoryStore
from winnie_assistant.search import SearchProvider

logger = logging.getLogger(__name__)

MAX_FETCH_BYTES = 2_000_000
MAX_REDIRECTS = 3
FETCH_USER_AGENT = "Mozilla/5.0 (compatible; Winnie/0.1; personal assistant)"

_SCRIPT = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class ToolError(RuntimeError):
    """A tool call that cannot be completed for a reportable reason."""


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information. Use it when the answer "
                "depends on facts after your training data, or on news, weather, "
                "prices or anything time-sensitive. Cite the result URL you used."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Number of results; default 5.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Fetch one public http(s) page and return its readable text. Use it "
                "after a search when the snippet is not enough."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute http(s) URL."}
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": (
                "Search the user's long-term memory: preferences, people, projects, "
                "decisions and earlier facts. Use it whenever the user refers to "
                "themselves or to something said before."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_remember",
            "description": (
                "Store one durable fact in long-term memory. Use it only when the "
                "user asks you to remember something, or states a stable "
                "preference, identity fact or relationship."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "identity",
                            "preferences",
                            "people",
                            "projects",
                            "domain",
                            "procedures",
                            "constraints",
                            "resources",
                            "episodes",
                            "unsorted",
                        ],
                    },
                    "text": {
                        "type": "string",
                        "description": "One self-contained sentence.",
                    },
                },
                "required": ["category", "text"],
                "additionalProperties": False,
            },
        },
    },
]


class ToolBox:
    def __init__(
        self,
        *,
        search: SearchProvider,
        memory: MemoryStore | None,
        http: httpx.AsyncClient,
        max_output_chars: int = 4000,
        fetch_timeout_seconds: float = 20.0,
    ) -> None:
        self.search = search
        self.memory = memory
        self.http = http
        self.max_output_chars = max_output_chars
        self.fetch_timeout_seconds = fetch_timeout_seconds

    async def execute(
        self, name: str, arguments: dict[str, Any], *, conversation_id: str
    ) -> str:
        try:
            if name == "web_search":
                output = await self._web_search(arguments)
            elif name == "fetch_page":
                output = await self._fetch_page(arguments)
            elif name == "memory_search":
                output = self._memory_search(arguments)
            elif name == "memory_remember":
                output = self._memory_remember(arguments, conversation_id)
            else:
                raise ToolError(f"unknown tool: {name}")
        except ToolError as exc:
            return f"ERROR: {exc}"
        except Exception as exc:  # noqa: BLE001 - a tool must not break the turn
            logger.exception("assistant tool %s failed", name)
            return f"ERROR: {type(exc).__name__}: {exc}"
        return _truncate(output, self.max_output_chars)

    # -- tools -----------------------------------------------------------

    async def _web_search(self, arguments: dict[str, Any]) -> str:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ToolError("web_search requires a non-empty query")
        limit = _as_int(arguments.get("limit"), default=5, minimum=1, maximum=10)
        results = await self.search.search(query, limit)
        if not results:
            raise ToolError("the search returned no results")
        lines = []
        for index, result in enumerate(results, start=1):
            lines.append(f"{index}. {result.title}")
            lines.append(f"   {result.url}")
            lines.append(f"   {result.snippet}")
        return "\n".join(lines)

    async def _fetch_page(self, arguments: dict[str, Any]) -> str:
        url = str(arguments.get("url") or "").strip()
        if not url:
            raise ToolError("fetch_page requires a url")
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await assert_public_url(current)
            try:
                response = await self.http.get(
                    current,
                    headers={"User-Agent": FETCH_USER_AGENT},
                    timeout=self.fetch_timeout_seconds,
                    follow_redirects=False,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise ToolError(f"fetch failed: {type(exc).__name__}") from exc
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ToolError("redirect without a location")
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                raise ToolError(f"fetch returned HTTP {response.status_code}")
            if len(response.content) > MAX_FETCH_BYTES:
                raise ToolError("page is larger than 2 MB")
            content_type = response.headers.get("content-type", "")
            if not any(
                marker in content_type
                for marker in ("text/html", "text/plain", "application/xhtml", "text/xml")
            ):
                raise ToolError(f"unsupported content type: {content_type or 'unknown'}")
            text = _html_to_text(response.text)
            if not text:
                raise ToolError("the page had no readable text")
            return text
        raise ToolError("too many redirects")

    def _memory_search(self, arguments: dict[str, Any]) -> str:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ToolError("memory_search requires a non-empty query")
        if self.memory is None:
            raise ToolError("memory is disabled")
        limit = _as_int(arguments.get("limit"), default=5, minimum=1, maximum=10)
        hits = self.memory.search(query, limit)
        if not hits:
            return "No memory entry matched that query."
        return "\n".join(
            f"- [{hit.entry.relpath}:{hit.entry.line}] {hit.entry.text}"
            for hit in hits
        )

    def _memory_remember(
        self, arguments: dict[str, Any], conversation_id: str
    ) -> str:
        if self.memory is None:
            raise ToolError("memory is disabled")
        category = str(arguments.get("category") or "").strip()
        text = str(arguments.get("text") or "").strip()
        if not text:
            raise ToolError("memory_remember requires text")
        added = self.memory.append(
            category, text, source=f"chat:{conversation_id}"
        )
        return "Remembered." if added else "That fact is already in memory."


async def assert_public_url(url: str) -> None:
    """Reject non-public targets before a request leaves the process.

    This is a best-effort SSRF guard: literal addresses and DNS results are
    checked, which covers the obvious cases. It cannot defeat DNS rebinding, and
    the assistant is a single-user local service, not a public proxy.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ToolError("only http and https URLs are allowed")
    host = parsed.hostname
    if not host:
        raise ToolError("the URL has no host")
    if host.lower() in {"localhost"} or host.lower().endswith(".localhost"):
        raise ToolError("localhost is not reachable")
    addresses = await _resolve(host)
    for address in addresses:
        candidate = ipaddress.ip_address(address.split("%", 1)[0])
        if (
            candidate.is_private
            or candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_multicast
            or candidate.is_reserved
            or candidate.is_unspecified
        ):
            raise ToolError(f"{host} resolves to a non-public address")


async def _resolve(host: str) -> set[str]:
    try:
        return {str(ipaddress.ip_address(host))}
    except ValueError:
        pass
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror as exc:
        raise ToolError(f"cannot resolve {host}") from exc
    return {info[4][0] for info in infos}


def _html_to_text(value: str) -> str:
    text = _SCRIPT.sub(" ", value)
    text = _COMMENT.sub(" ", text)
    text = re.sub(r"<(br|/p|/div|/li|/h[1-6])\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _SPACE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return _BLANK_LINES.sub("\n\n", text).strip()


def _as_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n… (truncated)"


def parse_arguments(raw: str) -> tuple[dict[str, Any], str]:
    """Parse tool arguments, returning ``(arguments, error)``."""
    if not raw.strip():
        return {}, ""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"arguments are not valid JSON: {exc.msg}"
    if not isinstance(value, dict):
        return {}, "arguments must be a JSON object"
    return value, ""
