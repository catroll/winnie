"""Record gateway calls in the shared usage ledger.

The gateway forwards the client's own vendor key, so it cannot ask a provider
for a balance; it only records what the response says. Usage is present in a
non-streaming response, in a streaming response only when the client asked the
provider to include it, and never in an error response.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from winnie_core.usage.event import UsageEvent
from winnie_core.usage.ledger import UsageSink
from winnie_core.usage.tokens import TokenCounts, merge_tokens, parse_usage
from winnie_gateway.forward import UpstreamOutcome
from winnie_gateway.routes import Route

logger = logging.getLogger(__name__)


def extract_json_usage(body: Mapping[str, Any]) -> TokenCounts:
    usage = body.get("usage")
    return parse_usage(usage if isinstance(usage, Mapping) else None)


def extract_stream_usage(chunk: Mapping[str, Any]) -> TokenCounts:
    """Read usage from one SSE chunk in either protocol's shape.

    OpenAI puts the final totals in a trailing ``usage`` field (only when
    ``stream_options.include_usage`` was requested). Anthropic puts input usage
    in ``message_start.message.usage`` and the final output count in a
    ``message_delta``'s ``usage``. Later chunks are cumulative, so the merge
    keeps the largest value seen.
    """
    tokens = TokenCounts()
    direct = chunk.get("usage")
    if isinstance(direct, Mapping):
        tokens = merge_tokens(tokens, parse_usage(direct))
    message = chunk.get("message")
    if isinstance(message, Mapping):
        nested = message.get("usage")
        if isinstance(nested, Mapping):
            tokens = merge_tokens(tokens, parse_usage(nested))
    return tokens


def usage_observer(
    *,
    usage: UsageSink | None,
    route: Route,
    model: str,
    protocol: str,
    stream: bool,
    endpoint: str,
) -> Callable[[UpstreamOutcome], None] | None:
    """Build the callback the forwarder invokes once per upstream call."""
    if usage is None:
        return None

    def observe(outcome: UpstreamOutcome) -> None:
        event = UsageEvent.create(
            tokens=outcome.tokens,
            at=datetime.now(timezone.utc),
            capability="gateway",
            source="gateway",
            provider=route.name,
            model=model,
            status=outcome.status,
            latency_ms=outcome.latency_ms,
            error=outcome.error[:500],
            detail={"protocol": protocol, "stream": stream, "endpoint": endpoint},
        )
        try:
            usage.record(event)
        except Exception:  # noqa: BLE001 - telemetry must not break the proxy
            logger.exception("failed to record gateway usage")

    return observe
