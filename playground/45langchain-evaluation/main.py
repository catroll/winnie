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

CASES=[
    {"q":"Winnie 知识用什么格式存？", "expect":"Markdown"},
    {"q":"默认向量库？", "expect":"Qdrant"},
]

def main():
    _ready()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    answer_chain=ChatPromptTemplate.from_messages([
        ("system","根据已知：知识 Markdown；向量库 Qdrant。短答。"),
        ("human","{q}"),
    ])|build_chat_model()|StrOutputParser()
    judge=ChatPromptTemplate.from_messages([
        ("system","判断 actual 是否覆盖 expect。只输出 YES 或 NO"),
        ("human","expect={expect}\nactual={actual}"),
    ])|build_chat_model()|StrOutputParser()
    for c in CASES:
        actual=answer_chain.invoke({"q":c["q"]}, config={"callbacks":[rec]})
        verdict=judge.invoke({"expect":c["expect"], "actual":actual}, config={"callbacks":[rec]}).strip().upper()
        print(f"Q:{c['q']}\nA:{actual}\nExpect:{c['expect']} Judge:{verdict}\n")
if __name__=="__main__":
    main()
