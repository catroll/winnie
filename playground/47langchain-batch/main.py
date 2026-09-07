from __future__ import annotations
import argparse, json, os, sys, time, hashlib
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

def main():
    _ready()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    chain=ChatPromptTemplate.from_messages([("human","用一词翻译:{word}")])|build_chat_model()|StrOutputParser()
    inputs=[{"word":w} for w in ["apple","banana","cat","dog"]]
    t0=time.perf_counter()
    outs=chain.batch(inputs, config={"callbacks":[rec]})
    print(f"batch n={len(inputs)} elapsed={time.perf_counter()-t0:.2f}s")
    for i,o in zip(inputs, outs):
        print(i["word"], "→", o)
if __name__=="__main__":
    main()
