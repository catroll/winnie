"""The two external services the pipeline needs.

Both are ports: the pipeline never constructs a client and never knows a vendor.
The host injects an LLM that resolves its model from the AI registry on every
call, and a search provider (the assistant's Bing RSS provider, or a paid API).

``purpose`` tells the adapter which registry binding to use
(``news.extract``, ``news.write``, ``news.review``) and lets the deterministic
fake answer offline tests without inspecting the prompt.
"""

from __future__ import annotations

from typing import Any, Protocol

from winnie_llm import LLMCompletion


class LLMPort(Protocol):
    async def complete(
        self, messages: list[dict[str, Any]], *, purpose: str = ""
    ) -> LLMCompletion: ...


class SearchPort(Protocol):
    name: str

    async def search(self, query: str, limit: int) -> list["SearchResult"]: ...


class SearchResult(Protocol):
    title: str
    url: str
    snippet: str


class LLMError(RuntimeError):
    """The model could not produce a usable answer for a pipeline stage."""
