"""One news model whose concrete supplier is resolved per call.

The news package asks for a purpose, not a vendor: extraction, writing and
review are separate registry bindings, so the cheap extraction model and the
careful writing model can differ. Resolving on every call means rebinding in the
console takes effect on the next run without a restart, exactly like the
assistant's model. The client itself comes from ``winnie-llm``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from winnie_core.usage.ledger import UsageSink
from winnie_llm import AIProviderError, LLMCompletion, ProviderRegistry
from winnie_news.fake import FakeNewsLLM
from winnie_api.usage.balances import BalanceService

# Purposes the pipeline uses. An unknown purpose falls back to extraction:
# the pipeline only ever sends these three, and a silent typo should still run.
_FEATURE_BY_PURPOSE = {
    "news.extract": "news.extract",
    "news.write": "news.write",
    "news.review": "news.review",
}

# Checked together: a run needs all three before it can finish.
_NEWS_FEATURES = ("news.extract", "news.write", "news.review")


class DynamicNewsLLM:
    name = "configured"

    def __init__(
        self,
        registry: ProviderRegistry,
        http: httpx.AsyncClient | None,
        usage: UsageSink | None = None,
        balances: BalanceService | None = None,
    ) -> None:
        self.registry = registry
        self.http = http
        self.usage = usage
        self.balances = balances

    @property
    def name(self) -> str:  # type: ignore[override]
        try:
            provider = self.registry.resolve("news.extract")
        except AIProviderError:
            return "unavailable"
        return provider.model or provider.integration_name

    def readiness(self) -> dict[str, Any]:
        """Whether every news purpose can actually make a call.

        A resolution failure (no binding, removed supplier, model not in the
        catalog) and a missing supplier key are different problems, but both
        make a run impossible and both are knowable before a batch is
        collected. Returning them here lets the service refuse the run with the
        concrete fix instead of failing at the first extraction.
        """
        features: list[dict[str, Any]] = []
        for feature in _NEWS_FEATURES:
            try:
                provider = self.registry.resolve(feature)
            except AIProviderError as exc:
                features.append(
                    {"feature": feature, "ready": False, "error": str(exc)}
                )
                continue
            needs_key = provider.kind != "fake"
            key_ready = not needs_key or self.registry.key_present(provider.vendor)
            balance_error = self._balance_error(provider)
            features.append(
                {
                    "feature": feature,
                    "ready": key_ready and not balance_error,
                    "vendor": provider.vendor,
                    "vendor_label": provider.vendor_label,
                    "model": provider.model,
                    "kind": provider.kind,
                    "key_env": provider.key_env if needs_key else "",
                    "key_present": key_ready,
                    "error": balance_error
                    or (
                        ""
                        if key_ready
                        else (
                            f"supplier {provider.vendor!r} has no API key; "
                            "set it in Admin -> AI suppliers"
                        )
                    ),
                }
            )
        return {
            "ready": all(feature["ready"] for feature in features),
            "features": features,
        }

    def _balance_error(self, provider: Any) -> str:
        """Refuse a run the account is already known not to be able to pay for.

        The snapshot is whatever the console last fetched, so this is advice,
        not a guarantee: no snapshot, a stale one, or a provider without a public
        balance endpoint is no opinion. A 402 that still arrives mid-run is
        handled by the pipeline, which stops on it.
        """
        if self.balances is None or provider.kind == "fake":
            return ""
        balance = self.balances.cached(provider.integration_id)
        if (
            balance is None
            or not balance.supported
            or balance.error
            or not balance.total
        ):
            return ""
        try:
            total = Decimal(balance.total)
        except InvalidOperation:
            return ""
        if total > 0:
            return ""
        currency = balance.currency or ""
        return (
            f"supplier {provider.vendor!r} reports a {balance.total} {currency} "
            "balance, so a run would stop at its first call; top it up first"
        )

    async def complete(
        self, messages: list[dict[str, Any]], *, purpose: str = ""
    ) -> LLMCompletion:
        feature = _FEATURE_BY_PURPOSE.get(purpose, "news.extract")
        provider = self.registry.resolve(feature)
        if provider.kind == "fake":
            return await FakeNewsLLM().complete(messages, purpose=purpose)
        if provider.kind == "openai":
            return await self.registry.chat_for(
                feature, http=self.http, usage=self.usage
            ).complete(messages)
        raise AIProviderError(
            f"{feature} cannot use a {provider.kind} supplier; choose an "
            "OpenAI-compatible one in Admin -> AI suppliers"
        )
