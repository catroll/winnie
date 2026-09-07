"""本章 Prompt 模板：普通 / 变量 / Few-shot / 动态（Placeholder + Partial）。"""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import (
    ChatPromptTemplate,
    FewShotChatMessagePromptTemplate,
    MessagesPlaceholder,
    PromptTemplate,
)

# —— 1) 普通字符串 PromptTemplate（非 Chat，演示最简变量填充）——
PLAIN_STRING = PromptTemplate.from_template(
    "用不超过两句中文解释概念「{concept}」，面向初学者。"
)

# —— 2) 变量 ChatPromptTemplate ——
VARIABLE_CHAT = ChatPromptTemplate.from_messages(
    [
        ("system", "你是{role}。回答语气：{tone}。只输出正文，不要标题。"),
        ("human", "主题：{topic}\n问题：{question}"),
    ]
)

# —— 3) Few-shot：先定「单条样例」形状，再嵌入正式对话 ——
_EXAMPLE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("human", "{input}"),
        ("ai", "{output}"),
    ]
)

_FEWSHOT_EXAMPLES = [
    {
        "input": "把『用户点了保存』写成一条记忆",
        "output": "用户偏好：重要操作后期望明确的成功反馈。",
    },
    {
        "input": "把『他讨厌长篇大论』写成一条记忆",
        "output": "沟通偏好：回答先给结论，避免冗长铺垫。",
    },
]

FEWSHOT_PROMPT = FewShotChatMessagePromptTemplate(
    example_prompt=_EXAMPLE_PROMPT,
    examples=_FEWSHOT_EXAMPLES,
)

FEWSHOT_CHAT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你把用户描述提炼成一条可写入知识库的短记忆。"
            "格式固定：类别：要点。参考下列样例的风格与粒度。",
        ),
        FEWSHOT_PROMPT,
        ("human", "{input}"),
    ]
)

# —— 4) 动态：MessagesPlaceholder 注入历史 + partial 固定部分变量 ——
DYNAMIC_CHAT = ChatPromptTemplate.from_messages(
    [
        ("system", "你是{product}助手。结合历史简洁回答；未知则说未知。"),
        MessagesPlaceholder("history", optional=True),
        ("human", "{question}"),
    ]
).partial(product="Winnie")

def build_model():
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    kwargs: dict = {"temperature": 0}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key
    return init_chat_model(f"openai:{model}", **kwargs)

def demo_history() -> list:
    """供 MessagesPlaceholder 注入的示例历史（动态 Prompt）。"""
    return [
        HumanMessage(content="我们向量库选定 Qdrant。"),
        AIMessage(content="已记下：检索层使用 Qdrant。"),
    ]
