"""组装 Model + Tools + System Prompt → Agent（ReAct / tool-calling 循环）。"""

import os

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

from agent.tools import TOOLS

SYSTEM_PROMPT = """你是 Winnie playground 里的示例助手。
你会使用工具完成任务：查时间、做计算、读写偏好。
先判断是否需要工具；需要则调用，再根据工具结果回答。
回答使用简体中文，简短直接。
"""


def build_agent():
    """创建 Agent 图：Model ⇄ Tools，直到不再发起 tool_calls。"""
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    kwargs: dict = {"temperature": 0}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key

    llm = init_chat_model(f"openai:{model}", **kwargs)
    return create_agent(
        model=llm,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )
