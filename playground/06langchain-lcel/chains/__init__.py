"""LCEL：用 | 组合 Prompt → Model → Parser（及再组合）。"""

from __future__ import annotations

import os
import re

from langchain.chat_models import init_chat_model
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

def build_model():
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    kwargs: dict = {"temperature": 0}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key
    return init_chat_model(f"openai:{model}", **kwargs)

PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "用简洁中文回答。第一行给结论，后面最多两句补充。"),
        ("human", "{question}"),
    ]
)

def build_basic_chain(model):
    """核心形态：prompt | model | parser。"""
    return PROMPT | model | StrOutputParser()

def _to_bullet_block(text: str) -> str:
    """后处理：把段落收成条目（演示 Composition：chain | lambda）。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return text
    head, *rest = lines
    bullets = [f"- {head}"]
    for ln in rest:
        ln = re.sub(r"^[•\-\d\.\)\s]+", "", ln)
        bullets.append(f"- {ln}")
    return "\n".join(bullets)

def build_composed_chain(model):
    """组合：把已有 chain 再 | 一个 RunnableLambda（复用 + 扩展）。"""
    basic = build_basic_chain(model)
    return basic | RunnableLambda(_to_bullet_block)

def build_from_template_chain(model):
    """同一 LCEL，换 Prompt 即换 Chain——组合优于复制整条调用代码。"""
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "只输出一个短句中文比喻，解释用户问题中的概念。"),
            ("human", "{question}"),
        ]
    )
    return prompt | model | StrOutputParser()
