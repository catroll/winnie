"""多轮会话：Model + 累积 Messages（上下文）+ 收尾总结。"""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain.messages import SystemMessage

SYSTEM_PROMPT = """你是 Winnie playground 的多轮对话助手。
规则：
- 结合完整历史上下文回答，可引用用户先前说过的信息。
- 每轮回复简短（2～4 句），使用简体中文。
- 不要假装调用工具；本示例没有工具。
- 当用户要求「总结」时，输出结构化 Markdown，覆盖：目标、已确定事项、待决问题、下一步建议。
"""

# 预设多轮用户发言（演示上下文依赖）；最后一轮由 main 追加「请总结」
DEMO_TURNS = [
    "我想做一个带长期记忆的个人助手，名字暂定 Winnie。",
    "服务层想用 FastAPI，知识用 Markdown，并用 Git 做版本。",
    "向量检索倾向 Qdrant；模型要能切换，不要锁死一家厂商。",
    "管理界面要能改文档并重新纳入知识库。两周内先做出可演示的对话+记忆闭环。",
]

SUMMARY_ASK = (
    "请根据以上全部对话，用 Markdown 输出一份结构化总结，章节固定为："
    "## 目标\n## 已确定事项\n## 待决问题\n## 下一步建议\n"
    "只写总结，不要寒暄。"
)


def build_model():
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    kwargs: dict = {"temperature": 0.2}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key
    return init_chat_model(f"openai:{model}", **kwargs)


def initial_messages() -> list:
    return [SystemMessage(content=SYSTEM_PROMPT)]
