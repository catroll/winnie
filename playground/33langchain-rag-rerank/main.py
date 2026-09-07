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


from _shared.rag_data import build_store

def naive_rerank(query: str, docs, top_n=3):
    # 教学用：按 query 词命中数重排（真生产用 cross-encoder）
    qw=set(query)
    scored=[]
    for d in docs:
        score=sum(1 for ch in qw if ch in d.page_content)
        scored.append((score, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _,d in scored[:top_n]]

def main():
    q="Git 版本 Markdown"
    docs=build_store().similarity_search(q, k=8)
    print("vector top8 → rerank top3")
    for d in naive_rerank(q, docs, 3):
        print("-", d.page_content)
if __name__=="__main__":
    main()
