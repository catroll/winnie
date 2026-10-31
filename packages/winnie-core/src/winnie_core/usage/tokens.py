"""Normalized token accounting for OpenAI- and Anthropic-shaped responses.

Both protocols report the same concepts under different field names, and they
disagree on one detail that matters for cost: OpenAI's ``prompt_tokens``
includes tokens served from the prompt cache, while Anthropic's
``input_tokens`` excludes cache reads and cache writes. The ledger stores a
normalized form so a price can be applied without knowing which protocol
produced the numbers:

    input_tokens         uncached prompt tokens, billed at the input rate
    cached_input_tokens  prompt tokens read from a prompt cache
    cache_write_tokens   prompt tokens written into a prompt cache
    output_tokens        generated tokens
    reasoning_tokens     a subset of output_tokens, for display only
    total_tokens         input + cached_input + cache_write + output

A field is ``None`` when the provider did not report it. That is deliberately
different from ``0``: a streamed response without a usage chunk must not look
like a free call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

TOKEN_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
    "provider_cost_micros",
)


@dataclass(frozen=True)
class TokenCounts:
    """Provider usage, normalized across OpenAI and Anthropic shapes."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    provider_cost_micros: int | None = None

    @property
    def reported(self) -> bool:
        """True when at least one billable token count was returned."""
        return any(
            value is not None
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.cached_input_tokens,
                self.cache_write_tokens,
                self.total_tokens,
            )
        )


def parse_usage(usage: Mapping[str, Any] | None) -> TokenCounts:
    """Normalize one ``usage`` object; an empty or missing object stays empty."""
    if not isinstance(usage, Mapping) or not usage:
        return TokenCounts()

    if "prompt_tokens" in usage or "completion_tokens" in usage:
        return _from_openai(usage)
    if "input_tokens" in usage or "output_tokens" in usage:
        return _from_anthropic(usage)
    total = _as_int(usage.get("total_tokens"))
    if total is not None:
        return TokenCounts(total_tokens=total)
    return TokenCounts()


def _from_openai(usage: Mapping[str, Any]) -> TokenCounts:
    prompt = _as_int(usage.get("prompt_tokens"))
    completion = _as_int(usage.get("completion_tokens"))
    cached = _nested_int(usage, "prompt_tokens_details", "cached_tokens")
    if cached is None:
        # DeepSeek reports the cache split at the top level.
        cached = _as_int(usage.get("prompt_cache_hit_tokens"))
    miss = _as_int(usage.get("prompt_cache_miss_tokens"))
    reasoning = _nested_int(
        usage, "completion_tokens_details", "reasoning_tokens"
    )
    if reasoning is None:
        reasoning = _as_int(usage.get("reasoning_tokens"))

    if miss is not None:
        input_tokens = miss
    elif prompt is not None:
        input_tokens = max(prompt - (cached or 0), 0)
    else:
        input_tokens = None

    total = _as_int(usage.get("total_tokens"))
    if total is None:
        total = _sum_present(input_tokens, cached, None, completion)
    return TokenCounts(
        input_tokens=input_tokens,
        output_tokens=completion,
        cached_input_tokens=cached,
        cache_write_tokens=None,
        reasoning_tokens=reasoning,
        total_tokens=total,
        provider_cost_micros=_cost_micros(usage.get("cost")),
    )


def _from_anthropic(usage: Mapping[str, Any]) -> TokenCounts:
    input_tokens = _as_int(usage.get("input_tokens"))
    output = _as_int(usage.get("output_tokens"))
    cached = _as_int(usage.get("cache_read_input_tokens"))
    cache_write = _as_int(usage.get("cache_creation_input_tokens"))
    total = _sum_present(input_tokens, cached, cache_write, output)
    return TokenCounts(
        input_tokens=input_tokens,
        output_tokens=output,
        cached_input_tokens=cached,
        cache_write_tokens=cache_write,
        reasoning_tokens=None,
        total_tokens=total,
        provider_cost_micros=_cost_micros(usage.get("cost")),
    )


def merge_tokens(left: TokenCounts, right: TokenCounts) -> TokenCounts:
    """Combine streamed usage, where later chunks carry cumulative totals.

    The token fields are merged with ``max`` because both supported protocols
    report cumulative counts. ``total_tokens`` is recomputed from the merged
    components instead: a first chunk may report only input tokens and a later
    one only output tokens, so the maximum of the two partial totals would be
    wrong.
    """
    merged: dict[str, int | None] = {}
    for name in TOKEN_FIELDS:
        former = getattr(left, name)
        latter = getattr(right, name)
        if former is None:
            merged[name] = latter
        elif latter is None:
            merged[name] = former
        else:
            merged[name] = max(former, latter)

    computed = _sum_present(
        merged["input_tokens"],
        merged["cached_input_tokens"],
        merged["cache_write_tokens"],
        merged["output_tokens"],
    )
    if computed is not None:
        merged["total_tokens"] = computed
    else:
        totals = [value for value in (left.total_tokens, right.total_tokens) if value is not None]
        merged["total_tokens"] = max(totals) if totals else None
    return TokenCounts(**merged)


def _sum_present(*values: int | None) -> int | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present)


def _nested_int(
    usage: Mapping[str, Any], section: str, key: str
) -> int | None:
    nested = usage.get(section)
    if not isinstance(nested, Mapping):
        return None
    return _as_int(nested.get(key))


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _cost_micros(value: Any) -> int | None:
    """Convert a provider-reported monetary cost into integer micro-units."""
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if amount < 0:
        return None
    return int((amount * 1_000_000).to_integral_value())
