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
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

@tool
def step_echo(x: str) -> str:
    """记录一步。"""
    return f"step-done:{x}"

def main():
    _ready()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    saver=InMemorySaver()
    agent=create_agent(model=build_chat_model(), tools=[step_echo],
        system_prompt="需要时调用 step_echo。", checkpointer=saver)
    cfg={"configurable":{"thread_id":"ckpt-1"}, "callbacks":[rec]}
    print("Step1")
    r1=agent.invoke({"messages":[{"role":"user","content":"请 step_echo hello"}]}, config=cfg)
    print(r1["messages"][-1].content)
    print("Step2 (resume same thread — history preserved)")
    r2=agent.invoke({"messages":[{"role":"user","content":"上一步做了什么？"}]}, config=cfg)
    print(r2["messages"][-1].content)
    print("checkpoint namespace=InMemorySaver (crash→resume 同理依赖持久化后端)")
if __name__=="__main__":
    main()
