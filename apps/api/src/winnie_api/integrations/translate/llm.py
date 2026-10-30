"""LLM-backed translator over an OpenAI-compatible chat endpoint."""

from __future__ import annotations

import json

from winnie_api.integrations.llm.json import parse_json_value
from winnie_api.integrations.llm.openai_compatible import OpenAICompatibleLLM
from winnie_api.integrations.translate.base import TranslationResult, Translator
from winnie_api.integrations.types import Usage


class LLMTranslator(Translator):
    def __init__(
        self,
        llm: OpenAICompatibleLLM,
        *,
        max_batch_chars: int = 6000,
    ) -> None:
        self.llm = llm
        self.max_batch_chars = max_batch_chars

    async def translate(
        self,
        texts: list[str],
        *,
        source: str | None,
        target: str,
    ) -> TranslationResult:
        if not texts:
            return TranslationResult(texts=[])

        translated: list[str] = []
        usages: list[Usage] = []
        for batch in _batches(texts, self.max_batch_chars):
            completion = await self.llm.complete(
                _messages(batch, source=source, target=target)
            )
            translated.extend(_parse_translations(completion.text, len(batch)))
            usages.append(completion.usage)
        return TranslationResult(texts=translated, usage=_merge_usage(usages))


def _batches(texts: list[str], max_chars: int) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for text in texts:
        size = len(text) + 1
        if current and current_chars + size > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(text)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _messages(
    texts: list[str],
    *,
    source: str | None,
    target: str,
) -> list[dict[str, str]]:
    payload = {
        "source_language": source or "auto-detect",
        "target_language": target,
        "lines": texts,
    }
    return [
        {
            "role": "system",
            "content": (
                "You are a professional meeting interpreter. Translate every "
                f"input line into {target}. Preserve the order and the exact "
                "number of lines. Return only a JSON array of translated "
                "strings, with no commentary or Markdown."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]


def _parse_translations(text: str, expected: int) -> list[str]:
    data = parse_json_value(text)
    if isinstance(data, dict):
        for key in ("translations", "lines", "result"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list) or len(data) != expected:
        raise RuntimeError(
            f"translator returned {len(data) if isinstance(data, list) else 'non-list'} "
            f"items for {expected} input lines"
        )

    result: list[str] = []
    for item in data:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            value = item.get("text") or item.get("translation") or ""
            result.append(str(value))
        else:
            result.append(str(item))
    return result


def _merge_usage(usages: list[Usage]) -> Usage | None:
    if not usages:
        return None
    return Usage(
        provider=usages[0].provider,
        unit=usages[0].unit,
        quantity=sum(item.quantity for item in usages),
    )
