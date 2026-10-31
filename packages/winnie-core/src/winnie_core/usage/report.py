"""Aggregate ledger events into the numbers the console displays."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from winnie_core.usage.event import UsageEvent
from winnie_core.usage.pricing import PriceTable, resolve_cost

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def empty_bucket() -> dict[str, int]:
    bucket = {"requests": 0, "errors": 0}
    for name in _TOKEN_FIELDS:
        bucket[name] = 0
    bucket.update(
        {
            "cost_micros": 0,
            "provider_priced_requests": 0,
            "estimated_requests": 0,
            "unpriced_requests": 0,
            "unreported_requests": 0,
        }
    )
    return bucket


def summarize(
    events: Iterable[UsageEvent],
    table: PriceTable,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    totals = empty_bucket()
    days: dict[str, dict[str, int]] = {}
    capabilities: dict[str, dict[str, int]] = {}
    models: dict[tuple[str, str], dict[str, int]] = {}

    for event in events:
        moment = event.at.astimezone(timezone.utc)
        day_key = moment.date().isoformat()
        capability_key = event.capability or "unknown"
        model_key = (event.provider or "unknown", event.model or "unknown")
        cost = resolve_cost(event, table)

        for bucket in (
            totals,
            days.setdefault(day_key, empty_bucket()),
            capabilities.setdefault(capability_key, empty_bucket()),
            models.setdefault(model_key, empty_bucket()),
        ):
            _add(bucket, event, cost.micros, cost.source)

    day_rows = [
        {"date": key, **bucket} for key, bucket in sorted(days.items())
    ]
    if start is not None and end is not None:
        day_rows = _fill_days(day_rows, start, end)

    capability_rows = [
        {"capability": key, **bucket} for key, bucket in capabilities.items()
    ]
    capability_rows.sort(
        key=lambda row: (-row["cost_micros"], -row["requests"], row["capability"])
    )

    model_rows = [
        {"provider": provider, "model": model, **bucket}
        for (provider, model), bucket in models.items()
    ]
    model_rows.sort(
        key=lambda row: (
            -row["cost_micros"],
            -row["total_tokens"],
            row["provider"],
            row["model"],
        )
    )

    return {
        "currency": table.currency,
        "totals": totals,
        "by_day": day_rows,
        "by_capability": capability_rows,
        "by_model": model_rows,
        "prices": table.describe(),
    }


def record_view(event: UsageEvent, table: PriceTable) -> dict[str, Any]:
    cost = resolve_cost(event, table)
    return {
        **event.to_json(),
        "has_usage": event.has_usage(),
        "cost_micros": cost.micros,
        "cost_source": cost.source,
        "cost_reason": cost.reason,
        "cost_currency": cost.currency,
    }


def filter_events(
    events: Iterable[UsageEvent],
    *,
    capability: str = "",
    provider: str = "",
    model: str = "",
    status: str = "",
) -> list[UsageEvent]:
    return [
        event
        for event in events
        if (not capability or event.capability == capability)
        and (not provider or event.provider == provider)
        and (not model or event.model == model)
        and (not status or event.status == status)
    ]


def _add(
    bucket: dict[str, int], event: UsageEvent, micros: int | None, source: str
) -> None:
    bucket["requests"] += 1
    if event.status != "ok":
        bucket["errors"] += 1
    for name in _TOKEN_FIELDS:
        value = getattr(event, name)
        if value is not None:
            bucket[name] += value
    if micros is not None:
        bucket["cost_micros"] += micros
        if source == "provider":
            bucket["provider_priced_requests"] += 1
        else:
            bucket["estimated_requests"] += 1
    elif source == "unpriced":
        bucket["unpriced_requests"] += 1
    else:
        bucket["unreported_requests"] += 1


def _fill_days(
    rows: list[dict[str, Any]], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Include days with no calls so a short range reads as a timeline."""
    present = {row["date"]: row for row in rows}
    first = start.astimezone(timezone.utc).date()
    last = end.astimezone(timezone.utc).date()
    filled: list[dict[str, Any]] = []
    current = first
    while current <= last:
        key = current.isoformat()
        filled.append(present.get(key) or {"date": key, **empty_bucket()})
        current += timedelta(days=1)
    return filled


def period(days: int, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return end - timedelta(days=days), end
