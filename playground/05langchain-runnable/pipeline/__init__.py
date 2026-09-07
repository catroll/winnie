"""Runnable 积木：Lambda / Sequence / Parallel / 标准 Pipeline。"""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import (
    RunnableLambda,
    RunnableParallel,
    RunnableSequence,
)


def build_model():
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    kwargs: dict = {"temperature": 0}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key
    return init_chat_model(f"openai:{model}", **kwargs)


def normalize_question(text: str) -> dict:
    """RunnableLambda：把原始字符串收成管道输入 dict。"""
    cleaned = " ".join(text.strip().split())
    return {"question": cleaned}


# —— Pipeline：Input → Prompt → Model → Parser ——
PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "用不超过三句中文回答。只输出正文。"),
        ("human", "{question}"),
    ]
)


def build_sequence_pipeline(model):
    """显式 RunnableSequence（与 prompt | model | parser 等价）。"""
    parser = StrOutputParser()
    return RunnableSequence(PROMPT, model, parser)


def build_pipe_pipeline(model):
    """LCEL 竖线写法：同一条 Pipeline 的语法糖（第 6 章会展开）。"""
    return PROMPT | model | StrOutputParser()


def build_with_lambda(model):
    """Lambda 预处理 + 管道：str → dict → Prompt → Model → Parser。"""
    prep = RunnableLambda(normalize_question)
    return prep | PROMPT | model | StrOutputParser()


def build_parallel(model):
    """RunnableParallel：同一输入扇出两路，再汇总。"""
    title_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "为用户问题起一个不超过 12 字的中文标题。只输出标题。"),
            ("human", "{question}"),
        ]
    )
    answer_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "用一两句中文直接回答。"),
            ("human", "{question}"),
        ]
    )
    branches = RunnableParallel(
        title=title_prompt | model | StrOutputParser(),
        answer=answer_prompt | model | StrOutputParser(),
        question=RunnableLambda(lambda x: x["question"]),
    )
    # 输入是 str 时先变成 {"question": ...}，再并行
    return RunnableLambda(normalize_question) | branches
