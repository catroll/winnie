"""Price model and cost estimation for recorded model calls.

Prices live in configuration, not in the ledger: a stored event keeps the token
counts the provider reported, so a corrected price also corrects the history.
All money is stored as integer micro-units (one millionth of the currency unit)
to avoid float drift in sums.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from winnie_core.usage.event import UsageEvent


@dataclass(frozen=True)
class Price:
    """Price per one million tokens, in the table's currency."""

    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None
    cache_write_per_million: Decimal | None = None


@dataclass(frozen=True)
class CostEstimate:
    micros: int | None
    currency: str
    reason: str = ""


@dataclass(frozen=True)
class Cost:
    """The cost that should be displayed for one event, and where it came from.

    ``source`` is ``provider`` when the vendor returned the cost with the
    response, ``estimated`` when it came from the local price table, and
    ``unpriced``/``unreported`` when no number can be shown honestly.
    """

    micros: int | None
    source: str
    reason: str
    currency: str


class PriceTable:
    """Model prices keyed by ``provider/model``, ``model`` or ``*``."""

    def __init__(
        self, prices: Mapping[str, Price] | None = None, *, currency: str = "USD"
    ) -> None:
        self.currency = currency.strip() or "USD"
        self._prices: dict[str, Price] = {}
        for key, price in (prices or {}).items():
            self._prices[key.strip().casefold()] = price

    @property
    def prices(self) -> dict[str, Price]:
        return dict(self._prices)

    def lookup(self, provider: str, model: str) -> Price | None:
        provider_key = (provider or "").strip().casefold()
        model_key = (model or "").strip().casefold()
        candidates: list[str] = []
        if provider_key and model_key:
            candidates.append(f"{provider_key}/{model_key}")
        if model_key:
            candidates.append(model_key)
        candidates.append("*")
        for key in candidates:
            price = self._prices.get(key)
            if price is not None:
                return price
        return None

    def estimate(self, event: UsageEvent) -> CostEstimate:
        if not event.has_usage():
            return CostEstimate(None, self.currency, "usage was not reported")
        price = self.lookup(event.provider, event.model)
        if price is None:
            return CostEstimate(
                None, self.currency, "no price is configured for this model"
            )
        if event.input_tokens is None or event.output_tokens is None:
            return CostEstimate(
                None,
                self.currency,
                "input and output tokens were not both reported",
            )

        amount = Decimal(0)
        amount += Decimal(event.input_tokens or 0) * price.input_per_million
        amount += Decimal(event.output_tokens or 0) * price.output_per_million
        cached = event.cached_input_tokens or 0
        if cached:
            if price.cached_input_per_million is None:
                return CostEstimate(
                    None,
                    self.currency,
                    "cached input tokens were reported but no cached price is configured",
                )
            amount += Decimal(cached) * price.cached_input_per_million
        written = event.cache_write_tokens or 0
        if written:
            if price.cache_write_per_million is None:
                return CostEstimate(
                    None,
                    self.currency,
                    "cache write tokens were reported but no cache write price is configured",
                )
            amount += Decimal(written) * price.cache_write_per_million
        # ``amount`` is tokens times price-per-million, which is already the
        # cost in micro-units: 1M tokens at 1.0 per million is 1,000,000 µ.
        micros = int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return CostEstimate(micros, self.currency)

    def describe(self) -> list[dict[str, object]]:
        """The configured table, for display in the console."""
        rows: list[dict[str, object]] = []
        for key, price in self._prices.items():
            rows.append(
                {
                    "match": key,
                    "input_per_million": float(price.input_per_million),
                    "output_per_million": float(price.output_per_million),
                    "cached_input_per_million": (
                        float(price.cached_input_per_million)
                        if price.cached_input_per_million is not None
                        else None
                    ),
                    "cache_write_per_million": (
                        float(price.cache_write_per_million)
                        if price.cache_write_per_million is not None
                        else None
                    ),
                }
            )
        return rows


def resolve_cost(event: UsageEvent, table: PriceTable) -> Cost:
    """Prefer a provider-reported cost, then the local table."""
    if event.provider_cost_micros is not None:
        return Cost(
            micros=event.provider_cost_micros,
            source="provider",
            reason="reported by the provider",
            currency=table.currency,
        )
    if not event.has_usage():
        return Cost(
            micros=None,
            source="unreported",
            reason="the provider did not return usage for this call",
            currency=table.currency,
        )
    estimate = table.estimate(event)
    if estimate.micros is None:
        return Cost(
            micros=None,
            source="unpriced",
            reason=estimate.reason,
            currency=table.currency,
        )
    return Cost(
        micros=estimate.micros,
        source="estimated",
        reason="estimated from the configured price table",
        currency=table.currency,
    )
