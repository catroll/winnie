"""Console API for recorded model usage, costs and provider balances.

The ledger is local telemetry: it answers "what did each model call cost and
how much did it use", separately from the gateway's request/response logs.
Recording is switched by ``usage.enabled``; the price table is read from
configuration on every request so a corrected price also corrects history.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from winnie_core.auth.dependencies import admin_required
from winnie_core.usage.event import UsageEvent
from winnie_core.usage.ledger import UsageLedger
from winnie_core.usage.pricing import PriceTable
from winnie_core.usage.report import filter_events, period, record_view, summarize

from winnie_api.usage.balances import BalanceService

router = APIRouter(
    prefix="/usage",
    tags=["usage"],
    dependencies=[Depends(admin_required)],
)


def _ledger(request: Request) -> UsageLedger:
    ledger = getattr(request.app.state, "usage_ledger", None)
    if ledger is None or not ledger.enabled:
        raise HTTPException(
            status_code=503,
            detail="usage recording is disabled (usage.enabled is false)",
        )
    return ledger


def _prices(request: Request) -> PriceTable:
    table = getattr(request.app.state, "usage_prices", None)
    if table is None:
        raise HTTPException(status_code=503, detail="usage pricing is not configured")
    return table


def _balances(request: Request) -> BalanceService:
    service = getattr(request.app.state, "usage_balances", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "provider balances need the AI provider registry "
                "(ai.enabled is false)"
            ),
        )
    return service


async def _load(
    request: Request, days: int
) -> tuple[list[UsageEvent], datetime, datetime]:
    start, end = period(days)
    events = await asyncio.to_thread(
        _ledger(request).read, start=start, end=end
    )
    return events, start, end


def _period_json(days: int, start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "days": days,
        "from": start.isoformat(timespec="seconds"),
        "to": end.isoformat(timespec="seconds"),
    }


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.get("/overview")
async def overview(
    request: Request,
    days: int = Query(default=7, ge=1, le=365),
) -> dict[str, Any]:
    """Totals plus by-day, by-capability and by-model breakdowns."""
    events, start, end = await _load(request, days)
    report = summarize(events, _prices(request), start=start, end=end)
    return {
        "period": _period_json(days, start, end),
        "generated_at": _generated_at(),
        **report,
    }


@router.get("/records")
async def records(
    request: Request,
    days: int = Query(default=7, ge=1, le=365),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    capability: str = Query(default="", max_length=64),
    provider: str = Query(default="", max_length=64),
    model: str = Query(default="", max_length=128),
    status: str = Query(default="", max_length=16),
) -> dict[str, Any]:
    """Recorded calls, newest first, with the cost that applies to each."""
    events, start, end = await _load(request, days)
    filtered = filter_events(
        events,
        capability=capability,
        provider=provider,
        model=model,
        status=status,
    )
    filtered.sort(key=lambda event: event.at, reverse=True)
    page = filtered[offset : offset + limit]
    table = _prices(request)
    return {
        "period": _period_json(days, start, end),
        "items": [record_view(event, table) for event in page],
        "total": len(filtered),
        "limit": limit,
        "offset": offset,
    }


@router.get("/balances")
async def balances(
    request: Request,
) -> dict[str, Any]:
    """Cached provider balances; the first read of a stale entry refreshes it."""
    return await _balances(request).snapshot(refresh=False)


@router.post("/balances/refresh")
async def refresh_balances(
    request: Request,
) -> dict[str, Any]:
    return await _balances(request).snapshot(refresh=True)
