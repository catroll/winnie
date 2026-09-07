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

@tool
def get_weather(city: str) -> str:
    """返回城市今明后三天温度。"""
    return f"{city}: 25,26,24"

@tool
def calculator(expression: str) -> str:
    """计算算术表达式。"""
    allowed=set("0123456789+-*/(). ,")
    if set(expression)-allowed: return "error"
    try: return str(eval(expression.replace(",",""), {"__builtins__":{}}, {}))
    except Exception as e: return f"error:{e}"

def main():
    _ready()
    p=argparse.ArgumentParser(); p.add_argument("-q", default="查北京天气，并计算未来三天平均温度"); a=p.parse_args()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    agent=create_agent(model=build_chat_model(), tools=[get_weather, calculator],
        system_prompt="先查天气再算平均；中文回答。")
    r=agent.invoke({"messages":[{"role":"user","content":a.q}]}, config={"callbacks":[rec]})
    print(r["messages"][-1].content)
if __name__=="__main__":
    main()
