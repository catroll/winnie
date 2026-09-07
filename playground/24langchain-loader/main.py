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

from common.rag_data import write_sample_files
from langchain_core.documents import Document

def load_text(path: Path) -> Document:
    return Document(page_content=path.read_text(encoding="utf-8"), metadata={"source": str(path)})

def load_csv(path: Path) -> list[Document]:
    lines=path.read_text(encoding="utf-8").strip().splitlines()
    header, *rows=lines
    return [Document(page_content=row, metadata={"source": str(path), "header": header}) for row in rows]

def main():
    # loader 可不需要 LLM
    data=ROOT/"data"; paths=write_sample_files(data)
    print("Loaded files:")
    for p in paths:
        if p.suffix==".csv":
            docs=load_csv(p)
        else:
            docs=[load_text(p)]
        print(f"- {p.name}: {len(docs)} Document(s)")
        print(" ", docs[0].page_content[:80].replace("\n"," "), "...")
if __name__=="__main__":
    main()
