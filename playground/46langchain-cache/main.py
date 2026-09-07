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

CACHE: dict[str,str] = {}

def main():
    _ready()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    chain=ChatPromptTemplate.from_messages([("human","{q}")])|build_chat_model()|StrOutputParser()
    q="用四个字解释缓存"
    for i in range(2):
        key=hashlib.sha256(q.encode()).hexdigest()
        if key in CACHE:
            print(f"[{i}] HIT", CACHE[key]); continue
        print(f"[{i}] MISS → LLM")
        ans=chain.invoke({"q":q}, config={"callbacks":[rec]})
        CACHE[key]=ans
        print(ans)
if __name__=="__main__":
    main()
