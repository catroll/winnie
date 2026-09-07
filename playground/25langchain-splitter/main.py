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

from common.rag_data import sample_documents, split_docs

def main():
    p=argparse.ArgumentParser(); p.add_argument("--size", type=int, default=80); p.add_argument("--overlap", type=int, default=16); a=p.parse_args()
    docs=sample_documents(); chunks=split_docs(docs, a.size, a.overlap)
    print(f"docs={len(docs)} → chunks={len(chunks)} (size={a.size}, overlap={a.overlap})")
    for i,c in enumerate(chunks[:6]):
        print(f"[{i}] {c.metadata.get('title')}#{c.metadata.get('chunk')}: {c.page_content!r}")
if __name__=="__main__":
    main()
