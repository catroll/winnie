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
    q="Winnie 技术栈？"
    prompt=ChatPromptTemplate.from_messages([
        ("system", "把问题扩展成 3 条不同角度的检索 query，每行一条，不要序号。"),
        ("human", "{q}"),
    ])
    text=(prompt|build_chat_model()|StrOutputParser()).invoke({"q":q}, config={"callbacks":[rec]})
    queries=[ln.strip("- ").strip() for ln in text.splitlines() if ln.strip()][:3]
    print("Queries:", queries)
    store=build_store(); seen=set(); merged=[]
    for qq in queries:
        for d in store.similarity_search(qq, k=2):
            key=d.page_content
            if key not in seen:
                seen.add(key); merged.append(d)
    print("Merged docs:", len(merged))
    for d in merged: print("-", d.page_content[:70])
if __name__=="__main__":
    main()
