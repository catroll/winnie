from __future__ import annotations
import argparse, json, os, sys, time, hashlib
from pathlib import Path
ROOT = Path(__file__).resolve().parent
from common.llm_record import LlmInteractionRecorder
from common.model import build_chat_model, load_env
EXAMPLE_ID = ROOT.name
LOGS_DIR = ROOT / "logs"

def _ready():
    load_env(ROOT)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY", file=sys.stderr); sys.exit(1)

from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

# 简易 Long-term store（进程内）
LONG_TERM: dict[str,str] = {}

def main():
    _ready()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    # short-term: checkpointer thread
    agent=create_agent(model=build_chat_model(), tools=[], system_prompt="短记忆会话助手", checkpointer=InMemorySaver())
    short_cfg={"configurable":{"thread_id":"s1"}, "callbacks":[rec]}
    r=agent.invoke({"messages":[{"role":"user","content":"我喜欢简洁回答"}]}, config=short_cfg)
    print("short:", r["messages"][-1].content)
    # long-term: 显式写入跨会话画像
    LONG_TERM["user:s1:style"]="简洁"
    print("long-term store:", LONG_TERM)
    # 新 thread，注入长期记忆
    style=LONG_TERM.get("user:s1:style","")
    agent2=create_agent(model=build_chat_model(), tools=[],
        system_prompt=f"用户偏好:{style}。", checkpointer=InMemorySaver())
    r2=agent2.invoke({"messages":[{"role":"user","content":"介绍一下你自己"}]},
        config={"configurable":{"thread_id":"s2"}, "callbacks":[rec]})
    print("new session with long-term:", r2["messages"][-1].content)
if __name__=="__main__":
    main()
