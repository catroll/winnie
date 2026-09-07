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

from common.rag_data import build_store

def main():
    p=argparse.ArgumentParser(); p.add_argument("-q", default="Winnie 用什么做版本管理？"); a=p.parse_args()
    retriever=build_store().as_retriever(search_kwargs={"k":3})
    docs=retriever.invoke(a.q)
    print("Question → Retriever → TopK")
    for d in docs:
        print("-", d.page_content)
if __name__=="__main__":
    main()
