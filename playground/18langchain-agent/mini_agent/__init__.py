"""最小 Agent：可无工具，专注 create_agent / Loop / State。"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.tools import tool

from common.model import build_chat_model

@tool
def now_hint() -> str:
    """返回固定时间提示（演示可选工具）。"""
    return "demo-time: weekday morning"

def build_plain_agent():
    """无工具：User → Agent → LLM → Answer。"""
    return create_agent(
        model=build_chat_model(),
        tools=[],
        system_prompt="你是简洁的中文助手。直接回答，不要假装调用工具。",
    )

def build_loop_agent():
    """带一个小工具：便于看到 Loop（model ↔ tool）。"""
    return create_agent(
        model=build_chat_model(),
        tools=[now_hint],
        system_prompt="若用户问时间相关，可调用 now_hint；否则直接答。中文短答。",
    )
