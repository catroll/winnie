from __future__ import annotations
import argparse, json, os, sys, time
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

def main():
    _ready()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    agent=create_agent(model=build_chat_model(), tools=[],
        system_prompt="记住用户自称；中文短答。", checkpointer=InMemorySaver())
    cfg={"configurable":{"thread_id":"demo-user"}, "callbacks":[rec]}
    for q in ["我叫 Woody", "我叫什么？"]:
        print("User:", q)
        r=agent.invoke({"messages":[{"role":"user","content":q}]}, config=cfg)
        print("AI:", r["messages"][-1].content)
if __name__=="__main__":
    main()
