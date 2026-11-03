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

from winnie_llm import LLMCompletion, LLMInsufficientBalance


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


class LLMBalanceExhausted(LLMError):
    """The supplier refused a call because the account cannot pay for it.

    This is not an event-level failure. Nothing else in the batch can succeed
    either, so the run stops and the record says why, instead of ending
    ``partial`` with an identical error per remaining item.
    """


def raise_if_balance_exhausted(exc: BaseException) -> None:
    """Propagate an unpaid call out of a per-item handler.

    The stages wrap a provider failure as ``LLMError(...) from exc``, so the
    original type survives only in the cause chain. Walking that chain is what
    keeps one 402 from becoming one swallowed failure per item in the batch.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, LLMInsufficientBalance):
            raise LLMBalanceExhausted(str(current)) from current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
