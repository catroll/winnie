from __future__ import annotations
import argparse, json, os, sys, time, hashlib
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

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

def main():
    _ready()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    prompt=ChatPromptTemplate.from_messages([("human","{q}")])
    primary=prompt|build_chat_model(model="this-model-does-not-exist-winnie")|StrOutputParser()
    secondary=prompt|build_chat_model()|StrOutputParser()
    chain=primary.with_fallbacks([secondary])
    print("GPT(假失败) → Fallback → 可用模型")
    print(chain.invoke({"q":"说你好"}, config={"callbacks":[rec]}))
if __name__=="__main__":
    main()
