from __future__ import annotations
import argparse, json, os, sys, time, hashlib
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))
from _shared.llm_record import LlmInteractionRecorder
from _shared.model import build_chat_model, load_env
EXAMPLE_ID = ROOT.name
LOGS_DIR = ROOT / "logs"

def _ready():
    load_env(ROOT)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY", file=sys.stderr); sys.exit(1)


from langchain.agents import create_agent
from langchain.tools import tool

ROLE_TOOLS = {
    "admin": ["weather", "delete_all"],
    "user": ["weather"],
    "guest": [],
}

@tool
def weather(city: str) -> str:
    """查天气。"""
    return f"{city}:晴"

@tool
def delete_all() -> str:
    """危险：清空数据（仅 admin）。"""
    return "ALL_DELETED"

def main():
    _ready()
    p=argparse.ArgumentParser(); p.add_argument("--role", choices=["admin","user","guest"], default="user"); a=p.parse_args()
    allowed=ROLE_TOOLS[a.role]
    tools=[]
    if "weather" in allowed: tools.append(weather)
    if "delete_all" in allowed: tools.append(delete_all)
    print(f"role={a.role} tools={[t.name for t in tools]}")
    if not tools:
        print("Guest 无工具，拒绝执行 Agent"); return
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    agent=create_agent(model=build_chat_model(), tools=tools, system_prompt=f"角色{a.role}，只用可用工具。")
    r=agent.invoke({"messages":[{"role":"user","content":"北京天气？也可以尝试清空"}]}, config={"callbacks":[rec]})
    print(r["messages"][-1].content)
if __name__=="__main__":
    main()
