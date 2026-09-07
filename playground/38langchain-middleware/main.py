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


from langchain_core.messages import SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

def before_model(payload: dict) -> dict:
    # Before Model：注入安全/风格约束
    payload = dict(payload)
    payload["question"] = payload["question"] + "（请简短）"
    print("[middleware:before]", payload["question"])
    return payload

def after_model(text: str) -> str:
    print("[middleware:after] len=", len(text))
    return text.strip()

def main():
    _ready()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    prompt=ChatPromptTemplate.from_messages([("system","中文回答"),("human","{question}")])
    chain=(RunnableLambda(before_model)|prompt|build_chat_model()|StrOutputParser()|RunnableLambda(after_model))
    print(chain.invoke({"question":"什么是 Middleware？"}, config={"callbacks":[rec]}))
if __name__=="__main__":
    main()
