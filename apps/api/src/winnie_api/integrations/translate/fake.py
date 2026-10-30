"""Deterministic translator for tests and local demos."""

from __future__ import annotations

from winnie_api.integrations.translate.base import TranslationResult, Translator
from winnie_api.integrations.types import Usage


class FakeTranslator(Translator):
    async def translate(
        self,
        texts: list[str],
        *,
        source: str | None,
        target: str,
    ) -> TranslationResult:
        return TranslationResult(
            texts=[f"译文（{target}）：{text}" for text in texts],
            usage=Usage(
                provider="fake-translator",
                unit="character",
                quantity=float(sum(len(text) for text in texts)),
            ),
        )
