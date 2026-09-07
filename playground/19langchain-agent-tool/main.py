from __future__ import annotations
import argparse, json, os, sys, time
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

@tool
def get_weather(city: str) -> str:
    """查询城市天气。"""
    return {"北京": "晴 25°C", "上海": "多云 27°C"}.get(city, f"{city}: n/a")

def main():
    _ready()
    p = argparse.ArgumentParser(); p.add_argument("-q", default="北京今天天气怎么样？"); a = p.parse_args()
    rec = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print("Agent = LLM + Tool + Loop"); print(f"[llm-record] {rec.dir}")
    agent = create_agent(model=build_chat_model(), tools=[get_weather],
                         system_prompt="需要天气时调用 get_weather；中文短答。")
    r = agent.invoke({"messages":[{"role":"user","content":a.q}]}, config={"callbacks":[rec]})
    print("User:", a.q); print("Assistant:", r["messages"][-1].content)
if __name__ == "__main__":
    main()
