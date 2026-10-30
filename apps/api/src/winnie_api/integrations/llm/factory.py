"""Internal LLM adapters and client construction."""

from __future__ import annotations

import os

from winnie_api.integrations.llm.openai_compatible import OpenAICompatibleLLM
from winnie_api.settings import Settings


def build_llm(settings: Settings) -> OpenAICompatibleLLM:
    config = settings.llm
    api_key = os.environ.get(config.api_key_env, "")
    if not api_key:
        raise RuntimeError(
            f"environment variable {config.api_key_env} is required for "
            "openai_compatible translation or summarization"
        )
    return OpenAICompatibleLLM(
        base_url=config.base_url,
        api_key=api_key,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        max_output_tokens=config.max_output_tokens,
        temperature=config.temperature,
        max_retries=config.max_retries,
        retry_backoff_seconds=config.retry_backoff_seconds,
    )
