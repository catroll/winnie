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


from _shared.rag_data import build_store, split_docs

def main():
    store=build_store(split_docs())
    hits=store.similarity_search("Qdrant 向量", k=2)
    print("store size ~", len(split_docs()), "chunks; top hits:")
    for h in hits:
        print("-", h.metadata, h.page_content[:60])
if __name__=="__main__":
    main()
