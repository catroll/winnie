"""Playground 共用：从环境变量构造 OpenAI 兼容 ChatModel。"""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model


def build_chat_model(*, temperature: float = 0, model: str | None = None):
    name = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    kwargs: dict = {"temperature": temperature}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key
    return init_chat_model(f"openai:{name}", **kwargs)


def load_env(example_root) -> None:
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv()
    if os.getenv("OPENAI_API_KEY"):
        return
    sibling = Path(example_root).resolve().parent / "01langchain-demo" / ".env"
    if sibling.exists():
        load_dotenv(sibling)
