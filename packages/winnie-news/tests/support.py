"""Shared test helpers: a scripted model and a fake search provider."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from winnie_core.llm.base import LLMCompletion
from winnie_core.types import Usage


class SearchHit:
    """The structural type the search port expects."""

    def __init__(self, title: str, url: str, snippet: str = "") -> None:
        self.title = title
        self.url = url
        self.snippet = snippet


class ScriptedLLM:
    """Answer each purpose from a queue of scripted replies.

    A reply is either JSON-serialisable or a callable receiving the messages,
    so a test can build an answer from the prompt it was actually given.
    """

    name = "scripted"

    def __init__(self, replies: dict[str, list[Any] | Any]) -> None:
        self._queues: dict[str, list[Any]] = defaultdict(list)
        for purpose, values in replies.items():
            self._queues[purpose] = (
                list(values) if isinstance(values, list) else [values]
            )
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    async def complete(
        self, messages: list[dict[str, Any]], *, purpose: str = ""
    ) -> LLMCompletion:
        self.calls.append((purpose, messages))
        queue = self._queues.get(purpose) or []
        if not queue:
            raise AssertionError(f"no scripted reply left for purpose {purpose!r}")
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        payload = reply(messages) if callable(reply) else reply
        if isinstance(payload, Exception):
            raise payload
        text = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False)
        )
        return LLMCompletion(
            text=text, model="scripted", usage=Usage("fake", "tokens", 0.0)
        )


class FakeSearchProvider:
    """Return per-query hits; a query mapped to an Exception raises it."""

    name = "fake"

    def __init__(self, results: dict[str, list[Any] | Exception]) -> None:
        self.results = results
        self.queries: list[str] = []

    async def search(self, query: str, limit: int):
        self.queries.append(query)
        value = self.results.get(query, [])
        if isinstance(value, Exception):
            raise value
        return value[:limit]
