"""Record gateway calls in the shared usage ledger.

The gateway holds the vendor key now, so it records what the response says and
attributes it to the Winnie key that made the call. Usage is present in a
non-streaming response, in a streaming response only when the client asked the
provider to include it, and never in an error response.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from dataclasses import dataclass

from winnie_core.usage.event import UsageEvent
from winnie_core.usage.ledger import UsageSink
from winnie_core.usage.tokens import TokenCounts, merge_tokens, parse_usage
from winnie_gateway.keystore import GatewayKey
from winnie_gateway.routes import Route

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpstreamOutcome:
    """What one upstream call did, for the usage ledger."""

    status_code: int | None
    latency_ms: int
    tokens: TokenCounts | None = None
    error: str = ""

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if self.status_code is None or not 200 <= self.status_code < 300:
            return "error"
        return "ok"


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
    key: GatewayKey,
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
            detail={
                "protocol": protocol,
                "stream": stream,
                "endpoint": endpoint,
                "key_id": key.id,
                "key_label": key.label,
            },
        )
        try:
            usage.record(event)
        except Exception:  # noqa: BLE001 - telemetry must not break the proxy
            logger.exception("failed to record gateway usage")

    return observe
