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
