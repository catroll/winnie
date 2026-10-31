"""One news model whose concrete provider is resolved per call.

The news package asks for a purpose, not a vendor: extraction, writing and
review are separate registry bindings, so the cheap extraction model and the
careful writing model can differ. Resolving on every call means rebinding in the
console takes effect on the next run without a restart, exactly like the
assistant's model.
"""

from __future__ import annotations

from typing import Any

import httpx

from winnie_core.llm.base import LLMCompletion
from winnie_core.usage.ledger import UsageSink
from winnie_news.fake import FakeNewsLLM

from winnie_api.ai.errors import AIProviderError
from winnie_api.ai.providers import _build_openai
from winnie_api.ai.store import AISettingsService

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
        ai: AISettingsService,
        http: httpx.AsyncClient | None,
        usage: UsageSink | None = None,
    ) -> None:
        self.ai = ai
        self.http = http
        self.usage = usage

    @property
    def name(self) -> str:  # type: ignore[override]
        try:
            provider = self.ai.resolve("news.extract")
        except AIProviderError:
            return "unavailable"
        return provider.model or provider.integration_name

    async def complete(
        self, messages: list[dict[str, Any]], *, purpose: str = ""
    ) -> LLMCompletion:
        feature = _FEATURE_BY_PURPOSE.get(purpose, "news.extract")
        provider = self.ai.resolve(feature)
        if provider.kind == "fake":
            return await FakeNewsLLM().complete(messages, purpose=purpose)
        if provider.kind == "openai":
            model = _build_openai(
                provider, self.ai.runtime(), self.http, self.usage
            )
            return await model.complete(messages)
        raise AIProviderError(
            f"{feature} cannot use a {provider.kind} integration; choose an "
            "OpenAI-compatible one in Admin -> AI providers"
        )
