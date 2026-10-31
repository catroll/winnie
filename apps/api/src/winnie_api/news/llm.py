"""One news model whose concrete supplier is resolved per call.

The news package asks for a purpose, not a vendor: extraction, writing and
review are separate registry bindings, so the cheap extraction model and the
careful writing model can differ. Resolving on every call means rebinding in the
console takes effect on the next run without a restart, exactly like the
assistant's model. The client itself comes from ``winnie-llm``.
"""

from __future__ import annotations

from typing import Any

import httpx

from winnie_core.usage.ledger import UsageSink
from winnie_llm import AIProviderError, LLMCompletion, ProviderRegistry
from winnie_news.fake import FakeNewsLLM

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
    ) -> None:
        self.registry = registry
        self.http = http
        self.usage = usage

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
            key_ready = not needs_key or self.registry.keys.present(provider.vendor)
            features.append(
                {
                    "feature": feature,
                    "ready": key_ready,
                    "vendor": provider.vendor,
                    "vendor_label": provider.vendor_label,
                    "model": provider.model,
                    "kind": provider.kind,
                    "key_env": provider.key_env if needs_key else "",
                    "key_present": key_ready,
                    "error": (
                        ""
                        if key_ready
                        else (
                            f"{provider.key_env} is not set for supplier "
                            f"{provider.vendor!r}; add it to "
                            f"{self.registry.keys.path}"
                        )
                    ),
                }
            )
        return {
            "ready": all(feature["ready"] for feature in features),
            "features": features,
        }

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
