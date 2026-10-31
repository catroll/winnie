"""Tools the assistant may call during a turn.

Every tool returns plain text that is safe to put in the model context: no
exceptions escape, no response is unbounded, and no tool has a side effect
outside the assistant's memory directory.
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

from winnie_assistant.index_client import IndexClient, IndexUnavailable
from winnie_assistant.memory import MemoryStore
from winnie_assistant.scheduler import AssistantScheduler, TaskDefinitionError
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

TASK_MANAGEMENT_TOOLS = frozenset({"schedule_task", "list_tasks", "cancel_task"})


class ToolError(RuntimeError):
    """A tool call that cannot be completed for a reportable reason."""


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public web for current information: news, weather, "
                "prices or anything after your training data. Cite the result "
                "URL you used."
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
                "Fetch one public http(s) page and return its readable text. Use "
                "it after a web search when the snippet is not enough."
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
            "name": "search_documents",
            "description": (
                "Search the owner's local documents: their notes, projects, and "
                "the assistant's own memory and past conversations (paths under "
                "Winnie/). Use it whenever the user refers to something said, "
                "written or decided before. Returns path, heading, line range "
                "and a snippet; cite path:line."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": (
                "Read a local document returned by search_documents, optionally "
                "a line window. The index revalidates the file before returning "
                "it, so the text is current."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path as returned by search_documents.",
                    },
                    "from_line": {"type": "integer", "minimum": 1},
                    "lines": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Store one durable fact in long-term memory (a Markdown file the "
                "owner can edit). Use it when the user asks you to remember "
                "something, or states a stable preference, identity fact or "
                "relationship."
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
    {
        "type": "function",
        "function": {
            "name": "schedule_task",
            "description": (
                "Create a recurring task that runs on its own and writes the "
                "result to a conversation. Only call this after the user has "
                "explicitly agreed to the schedule and to what the task should "
                "do; never create one from a passing remark. Schedule forms: "
                "'every 15m' (minimum 5m), 'daily 07:30', 'weekly mon 09:00'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short label."},
                    "schedule": {
                        "type": "string",
                        "description": "e.g. 'daily 07:30' or 'every 2h'.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": (
                            "The instruction the task runs at each fire time, "
                            "written as a message to yourself."
                        ),
                    },
                    "webhook": {
                        "type": "string",
                        "description": (
                            "Optional http(s) URL that receives the result, for "
                            "phone delivery."
                        ),
                    },
                },
                "required": ["title", "schedule", "prompt"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List scheduled tasks with id, schedule, next run and last status.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_task",
            "description": (
                "Cancel one scheduled task by id. Confirm which task with the "
                "user first; this deletes it and its schedule."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Task id from list_tasks."}
                },
                "required": ["id"],
                "additionalProperties": False,
            },
        },
    },
]


def tool_schemas(*, task_management: bool = True) -> list[dict[str, Any]]:
    """Tool schemas for a turn.

    A task run must not create more tasks, so management tools are left out of
    scheduled turns; that removes the recursive-schedule failure mode instead
    of asking the model to behave.
    """
    if task_management:
        return list(TOOL_SCHEMAS)
    return [
        schema
        for schema in TOOL_SCHEMAS
        if schema["function"]["name"] not in TASK_MANAGEMENT_TOOLS
    ]


class ToolBox:
    def __init__(
        self,
        *,
        search: SearchProvider,
        index: IndexClient | None,
        memory: MemoryStore | None,
        scheduler: AssistantScheduler | None,
        http: httpx.AsyncClient,
        tasks_enabled: bool = True,
        max_output_chars: int = 4000,
        fetch_timeout_seconds: float = 20.0,
    ) -> None:
        self.search = search
        self.index = index
        self.memory = memory
        self.scheduler = scheduler
        self.http = http
        self.tasks_enabled = tasks_enabled
        self.max_output_chars = max_output_chars
        self.fetch_timeout_seconds = fetch_timeout_seconds

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        conversation_id: str,
        allow_task_management: bool = True,
    ) -> str:
        try:
            if name in TASK_MANAGEMENT_TOOLS and not allow_task_management:
                raise ToolError(
                    "scheduled tasks cannot be managed from inside a task run"
                )
            if name == "web_search":
                output = await self._web_search(arguments)
            elif name == "fetch_page":
                output = await self._fetch_page(arguments)
            elif name == "search_documents":
                output = await self._search_documents(arguments)
            elif name == "read_document":
                output = await self._read_document(arguments)
            elif name == "remember":
                output = self._remember(arguments, conversation_id)
            elif name == "schedule_task":
                output = await self._schedule_task(arguments)
            elif name == "list_tasks":
                output = self._list_tasks()
            elif name == "cancel_task":
                output = await self._cancel_task(arguments)
            else:
                raise ToolError(f"unknown tool: {name}")
        except ToolError as exc:
            return f"ERROR: {exc}"
        except Exception as exc:  # noqa: BLE001 - a tool must not break the turn
            logger.exception("assistant tool %s failed", name)
            return f"ERROR: {type(exc).__name__}: {exc}"
        return _truncate(output, self.max_output_chars)

    # -- web -------------------------------------------------------------

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

    # -- local documents -------------------------------------------------

    async def _search_documents(self, arguments: dict[str, Any]) -> str:
        if self.index is None:
            raise ToolError(
                "the local index is disabled; set assistant.index.enabled to use it"
            )
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ToolError("search_documents requires a non-empty query")
        limit = _as_int(arguments.get("limit"), default=8, minimum=1, maximum=20)
        try:
            hits = await self.index.search(query, limit=limit)
        except IndexUnavailable as exc:
            raise ToolError(str(exc)) from exc
        if not hits:
            return "No local document matched that query."
        lines: list[str] = []
        for index, hit in enumerate(hits, start=1):
            heading = f" — {hit.heading}" if hit.heading else ""
            lines.append(
                f"{index}. {hit.path}:{hit.start_line}-{hit.end_line}{heading}"
                f" (modified {hit.mtime})"
            )
            lines.append(f"   {hit.snippet}")
            if hit.also_at:
                lines.append(f"   also at: {', '.join(hit.also_at)}")
        return "\n".join(lines)

    async def _read_document(self, arguments: dict[str, Any]) -> str:
        if self.index is None:
            raise ToolError(
                "the local index is disabled; set assistant.index.enabled to use it"
            )
        path = str(arguments.get("path") or "").strip()
        if not path:
            raise ToolError("read_document requires a path")
        from_line = _as_int(arguments.get("from_line"), default=1, minimum=1, maximum=100000)
        lines = arguments.get("lines")
        try:
            document = await self.index.read(
                path,
                from_line=from_line,
                lines=_as_int(lines, default=0, minimum=1, maximum=200) if lines else None,
            )
        except IndexUnavailable as exc:
            raise ToolError(str(exc)) from exc
        text = str(document.get("text") or "")
        if not text.strip():
            return f"{path}: no readable text"
        start = int(document.get("from_line") or from_line)
        total = int(document.get("total_lines") or 0)
        header = f"{path} lines {start}-{start + text.count(chr(10))} of {total}"
        if document.get("fresh") is False:
            header += " (reindexed just now)"
        return f"{header}\n\n{text}"

    # -- memory ----------------------------------------------------------

    def _remember(self, arguments: dict[str, Any], conversation_id: str) -> str:
        if self.memory is None:
            raise ToolError("memory is disabled")
        category = str(arguments.get("category") or "").strip()
        text = str(arguments.get("text") or "").strip()
        if not text:
            raise ToolError("remember requires text")
        added = self.memory.append(
            category, text, source=f"chat:{conversation_id}"
        )
        return "Remembered." if added else "That fact is already in memory."

    # -- scheduled tasks -------------------------------------------------

    async def _schedule_task(self, arguments: dict[str, Any]) -> str:
        scheduler = self._scheduler()
        try:
            task, next_run = await scheduler.create_task(
                title=str(arguments.get("title") or ""),
                schedule=str(arguments.get("schedule") or ""),
                prompt=str(arguments.get("prompt") or ""),
                webhook=str(arguments.get("webhook") or ""),
            )
        except TaskDefinitionError as exc:
            raise ToolError(str(exc)) from exc
        return (
            f"Created task {task.id} ({task.title}) with schedule '{task.schedule}'."
            f" Next run: {next_run or 'pending'}. Tell the user the id so they can"
            " cancel it later."
        )

    def _list_tasks(self) -> str:
        scheduler = self._scheduler()
        if not scheduler.tasks:
            return "No scheduled tasks."
        lines = []
        for task in scheduler.tasks:
            state = scheduler.state_for(task.id)
            lines.append(
                f"- {task.id} · {task.title} · {task.schedule}"
                f" · next {state.get('next_run') or 'disabled'}"
                f" · last {state.get('last_status') or 'never'}"
            )
        return "\n".join(lines)

    async def _cancel_task(self, arguments: dict[str, Any]) -> str:
        scheduler = self._scheduler()
        task_id = str(arguments.get("id") or "").strip()
        if not task_id:
            raise ToolError("cancel_task requires an id")
        removed = await scheduler.delete_task(task_id)
        if not removed:
            raise ToolError(f"no scheduled task with id {task_id}")
        return f"Cancelled task {task_id}."

    def _scheduler(self) -> AssistantScheduler:
        if self.scheduler is None or not self.tasks_enabled:
            raise ToolError("scheduled tasks are disabled in the configuration")
        return self.scheduler


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
