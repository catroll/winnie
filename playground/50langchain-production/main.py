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
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from common.rag_data import build_store

@tool
def knowledge(query: str) -> str:
    """知识库检索。"""
    return "\n".join(d.page_content for d in build_store().similarity_search(query, k=2))

def main():
    _ready()
    print("""
AI Assistant Platform (sketch)
  API → Gateway → LangChain Agent
           ├─ RAG Tool
           ├─ Memory(thread)
           └─ Observability(logs)
""")
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    # gateway-ish: tags + fallback-ready chain for health
    health=ChatPromptTemplate.from_messages([("human","ping")])|build_chat_model()|StrOutputParser()
    print("health:", health.invoke({}, config={"tags":["prod","health"], "callbacks":[rec]}))
    agent=create_agent(model=build_chat_model(), tools=[knowledge],
        system_prompt="生产助手：优先 knowledge。")
    r=agent.invoke({"messages":[{"role":"user","content":"知识如何存储？"}]},
        config={"tags":["prod","chat"], "metadata":{"tenant":"demo"}, "callbacks":[rec]})
    print("chat:", r["messages"][-1].content)
    print("logs →", rec.dir)
if __name__=="__main__":
    main()
