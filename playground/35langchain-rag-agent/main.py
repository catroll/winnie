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
from _shared.rag_data import build_store

store=None
def get_store():
    global store
    if store is None: store=build_store()
    return store

@tool
def knowledge_search(query: str) -> str:
    """在 Winnie 知识库中检索相关段落。"""
    docs=get_store().similarity_search(query, k=3)
    return "\n".join(f"- {d.page_content}" for d in docs) or "no hit"

def main():
    _ready()
    p=argparse.ArgumentParser(); p.add_argument("-q", default="知识库用什么向量库？"); a=p.parse_args()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    agent=create_agent(model=build_chat_model(), tools=[knowledge_search],
        system_prompt="优先 knowledge_search；中文短答。")
    r=agent.invoke({"messages":[{"role":"user","content":a.q}]}, config={"callbacks":[rec]})
    print(r["messages"][-1].content)
if __name__=="__main__":
    main()
