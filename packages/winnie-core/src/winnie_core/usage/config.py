"""Configuration for usage recording, pricing and balance snapshots."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from winnie_core.usage.pricing import Price, PriceTable


class ModelPriceConfig(BaseModel):
    """One price row: currency units per one million tokens."""

    model_config = ConfigDict(extra="forbid")

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cached_input: float | None = Field(default=None, ge=0)
    cache_write: float | None = Field(default=None, ge=0)


class UsageConfig(BaseModel):
    """Local ledger and the estimate table behind the console's usage page.

    Disabled by default so a programmatic ``Settings()`` (tests, embedders)
    never writes into the checkout's ``data/`` directory; ``config.yaml`` turns
    it on for the service, like ``ai.enabled``.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Relative paths are resolved against apps/api/, like every other root.
    root: str = "../../data/usage"
    # Currency of the price table. Provider-reported costs are assumed to use
    # the same unit; the shipped default (USD) matches the vendors that report
    # a cost with the response.
    currency: str = "USD"
    prices: dict[str, ModelPriceConfig] = Field(default_factory=dict)
    # How long a provider balance snapshot stays fresh before the console's
    # next read refreshes it.
    balance_ttl_seconds: int = Field(default=300, ge=0, le=86400)
    balance_timeout_seconds: float = Field(default=8.0, ge=1.0, le=60.0)

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("usage.currency must not be empty")
        if len(cleaned) > 8 or not cleaned.replace("-", "").isalnum():
            raise ValueError("usage.currency must be a short currency code")
        return cleaned

    def price_table(self) -> PriceTable:
        prices = {
            key: Price(
                input_per_million=Decimal(str(entry.input)),
                output_per_million=Decimal(str(entry.output)),
                cached_input_per_million=(
                    Decimal(str(entry.cached_input))
                    if entry.cached_input is not None
                    else None
                ),
                cache_write_per_million=(
                    Decimal(str(entry.cache_write))
                    if entry.cache_write is not None
                    else None
                ),
            )
            for key, entry in self.prices.items()
        }
        return PriceTable(prices, currency=self.currency)
