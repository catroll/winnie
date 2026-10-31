"""Translation provider port and result type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from winnie_core.types import Usage


@dataclass(frozen=True)
class TranslationResult:
    texts: list[str]
    usage: Usage | None = None


class Translator(Protocol):
    async def translate(
        self,
        texts: list[str],
        *,
        source: str | None,
        target: str,
    ) -> TranslationResult:
        """Translate texts in order, preserving input order and count."""
        ...
