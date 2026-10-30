"""Provider-neutral LLM access for internal meeting jobs."""

from __future__ import annotations

from dataclasses import dataclass

from winnie_api.integrations.types import Usage


@dataclass(frozen=True)
class LLMCompletion:
    text: str
    model: str
    usage: Usage
