"""流式管道与 Token 回调。"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

def build_model():
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    kwargs: dict = {"temperature": 0.4}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key
    return init_chat_model(f"openai:{model}", **kwargs)

PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "用中文回答，分 3～5 短句，句与句之间换行。不要用 Markdown 标题。",
        ),
        ("human", "{question}"),
    ]
)

def build_chain(model):
    """LCEL：流式时 parser 会透传 token 文本块。"""
    return PROMPT | model | StrOutputParser()

class TokenPrintHandler(BaseCallbackHandler):
    """Callback：在 LLM 新 token 到达时打印（演示 Token Streaming 钩子）。"""

    def __init__(self) -> None:
        super().__init__()
        self.tokens: list[str] = []
        self._index = 0

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        if not token:
            return
        self._index += 1
        self.tokens.append(token)
        # 模拟「Token1 Token2 …」可观测性；真实 Web 可改写成 SSE yield
        printable = token.replace("\n", "\\n")
        print(f"Token{self._index}: {printable!r}")

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        print("[callback] chat model start")

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        print(f"[callback] llm end  (tokens collected: {len(self.tokens)})")
