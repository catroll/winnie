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
from langchain.tools import tool
from langchain.messages import AIMessage, ToolMessage

@tool
def ping(x: str) -> str:
    """回显。"""
    return f"pong:{x}"

def main():
    _ready()
    p=argparse.ArgumentParser(); p.add_argument("-q", default="请 ping 一下 winnie"); a=p.parse_args()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    agent=create_agent(model=build_chat_model(), tools=[ping], system_prompt="需要时调用 ping。")
    r=agent.invoke({"messages":[{"role":"user","content":a.q}]}, config={"callbacks":[rec]})
    step=0
    for m in r["messages"]:
        if isinstance(m, AIMessage) and m.tool_calls:
            step+=1
            print("="*16, f"STEP {step} LLM → Tool Call"); print(m.tool_calls)
        elif isinstance(m, ToolMessage):
            print("="*16, f"STEP {step} Tool Result → LLM"); print(m.content)
        elif isinstance(m, AIMessage) and m.content:
            print("="*16, "FINAL"); print(m.content)
if __name__=="__main__":
    main()
