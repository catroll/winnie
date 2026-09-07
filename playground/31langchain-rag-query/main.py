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


from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from _shared.rag_data import build_store

def main():
    _ready()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    history="User: 介绍 Winnie\nAI: 长期记忆助手。"
    follow="它多少钱？"  # 指代不清
    rewrite_prompt=ChatPromptTemplate.from_messages([
        ("system", "把后续问题改写成独立检索问句，只输出问句。"),
        ("human", "历史:\n{history}\n后续:{question}"),
    ])
    rewritten=(rewrite_prompt|build_chat_model()|StrOutputParser()).invoke(
        {"history":history, "question":follow}, config={"callbacks":[rec]})
    print("Follow-up:", follow)
    print("Rewrite:", rewritten)
    docs=build_store().as_retriever(search_kwargs={"k":2}).invoke(rewritten)
    print("Hits:")
    for d in docs: print("-", d.page_content)
if __name__=="__main__":
    main()
