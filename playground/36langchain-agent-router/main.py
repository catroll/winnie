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
from common.rag_data import build_store

@tool
def weather(city: str) -> str:
    """查天气。"""
    return f"{city}: 晴"

@tool
def knowledge(query: str) -> str:
    """查公司/产品知识。"""
    docs=build_store().similarity_search(query, k=2)
    return "\n".join(d.page_content for d in docs)

@tool
def orders(order_id: str) -> str:
    """查订单。"""
    return {"1001":"已发货","1002":"待支付"}.get(order_id, "not found")

def main():
    _ready()
    p=argparse.ArgumentParser(); p.add_argument("-q", action="append", dest="qs"); a=p.parse_args()
    qs=a.qs or ["北京天气？", "Winnie 知识怎么存？", "订单 1001 状态？"]
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    agent=create_agent(model=build_chat_model(), tools=[weather, knowledge, orders],
        system_prompt="路由：天气→weather；产品知识→knowledge；订单→orders。")
    for q in qs:
        print("\nQ:", q)
        r=agent.invoke({"messages":[{"role":"user","content":q}]}, config={"callbacks":[rec]})
        print("A:", r["messages"][-1].content)
if __name__=="__main__":
    main()
