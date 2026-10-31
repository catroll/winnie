"""One recorded model call, as appended to the usage ledger."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from winnie_core.usage.tokens import TOKEN_FIELDS, TokenCounts


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class UsageEvent:
    """A single billable (or attempted) model call.

    ``status`` is ``ok`` or ``error``. An interrupted stream is recorded as an
    error with the partial token counts that did arrive. ``provider`` is the
    admin integration or gateway route name, not a vendor guess, so the same
    model reached through two routes stays attributable.
    """

    at: datetime
    capability: str
    source: str
    provider: str
    model: str
    status: str = "ok"
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    provider_cost_micros: int | None = None
    error: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @classmethod
    def create(
        cls, *, tokens: TokenCounts | None = None, **kwargs: Any
    ) -> UsageEvent:
        for name in TOKEN_FIELDS:
            kwargs.setdefault(name, getattr(tokens, name) if tokens else None)
        return cls(**kwargs)

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "at": self.at.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "capability": self.capability,
            "source": self.source,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "provider_cost_micros": self.provider_cost_micros,
            "error": self.error,
            "detail": self.detail,
        }
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> UsageEvent:
        raw_at = payload.get("at")
        at = _parse_datetime(raw_at)
        detail = payload.get("detail")
        return cls(
            id=str(payload.get("id") or uuid.uuid4().hex),
            at=at,
            capability=str(payload.get("capability") or ""),
            source=str(payload.get("source") or ""),
            provider=str(payload.get("provider") or ""),
            model=str(payload.get("model") or ""),
            status=str(payload.get("status") or "ok"),
            latency_ms=_as_int(payload.get("latency_ms")),
            input_tokens=_as_int(payload.get("input_tokens")),
            output_tokens=_as_int(payload.get("output_tokens")),
            cached_input_tokens=_as_int(payload.get("cached_input_tokens")),
            cache_write_tokens=_as_int(payload.get("cache_write_tokens")),
            reasoning_tokens=_as_int(payload.get("reasoning_tokens")),
            total_tokens=_as_int(payload.get("total_tokens")),
            provider_cost_micros=_as_int(payload.get("provider_cost_micros")),
            error=str(payload.get("error") or ""),
            detail=detail if isinstance(detail, dict) else {},
        )

    def has_usage(self) -> bool:
        return any(
            getattr(self, name) is not None
            for name in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cache_write_tokens",
                "total_tokens",
            )
        )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = utcnow()
    else:
        parsed = utcnow()
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
