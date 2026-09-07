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


import asyncio
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

TOKEN_BUDGET=200
_sem=None

async def limited_ainvoke(chain, payload, config, sem):
    async with sem:
        return await chain.ainvoke(payload, config=config)

def main():
    _ready()
    p=argparse.ArgumentParser(); p.add_argument("--concurrency", type=int, default=2); a=p.parse_args()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    prompt=ChatPromptTemplate.from_messages([("human","{q}")])
    chain=prompt|build_chat_model()|StrOutputParser()
    qs=[f"用一句话解释概念{i}" for i in range(4)]
    async def run():
        sem=asyncio.Semaphore(a.concurrency)
        print(f"Rate limit concurrency={a.concurrency}; token budget demo={TOKEN_BUDGET}")
        tasks=[limited_ainvoke(chain, {"q":q}, {"callbacks":[rec]}, sem) for q in qs]
        return await asyncio.gather(*tasks)
    for i,ans in enumerate(asyncio.run(run()),1):
        print(i, ans[:60])
if __name__=="__main__":
    main()
