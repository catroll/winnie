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
def research_agent(topic: str) -> str:
    """研究型子代理：返回主题要点。"""
    return f"[research] {topic}: 要点A; 要点B"

@tool
def writing_agent(outline: str) -> str:
    """写作型子代理：根据提纲写一段。"""
    return f"[writing] 基于「{outline}」的短文草稿……"

def main():
    _ready()
    p=argparse.ArgumentParser(); p.add_argument("-q", default="研究 Winnie 记忆架构并写一段介绍"); a=p.parse_args()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    supervisor=create_agent(model=build_chat_model(), tools=[research_agent, writing_agent],
        system_prompt="你是 Supervisor：先 research 再 writing；中文。")
    r=supervisor.invoke({"messages":[{"role":"user","content":a.q}]}, config={"callbacks":[rec]})
    print(r["messages"][-1].content)
if __name__=="__main__":
    main()
