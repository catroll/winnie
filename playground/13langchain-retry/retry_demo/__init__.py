"""Retry / Timeout / Fallback 积木。"""

from __future__ import annotations

import os
from itertools import count

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from common.model import build_chat_model

PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "用一句中文回答。"),
        ("human", "{question}"),
    ]
)

def build_primary_chain():
    return PROMPT | build_chat_model() | StrOutputParser()

def build_fallback_chain():
    """主链故意用无效模型 → 失败后 Fallback 到可用模型。"""
    bad = build_chat_model(model="this-model-does-not-exist-winnie")
    good = build_chat_model(
        model=os.getenv("OPENAI_MODEL_FALLBACK") or os.getenv("OPENAI_MODEL")
    )
    primary = PROMPT | bad | StrOutputParser()
    secondary = PROMPT | good | StrOutputParser()
    return primary.with_fallbacks([secondary])

def build_retry_lambda(*, fail_times: int = 2):
    """前 N 次抛错，之后成功 —— 演示 with_retry。"""
    attempts = count(1)

    def flaky(text: str) -> str:
        n = next(attempts)
        print(f"  [attempt {n}]")
        if n <= fail_times:
            raise ConnectionError(f"simulated failure #{n}")
        return f"ok after {n} attempts: {text}"

    return RunnableLambda(flaky).with_retry(
        stop_after_attempt=fail_times + 2,
        wait_exponential_jitter=False,
    )
