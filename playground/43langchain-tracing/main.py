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


from uuid import uuid4
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig

def main():
    _ready()
    rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    trace_id=str(uuid4())
    prompt=ChatPromptTemplate.from_messages([("human","{q}")])
    chain=prompt|build_chat_model()|StrOutputParser()
    cfg=RunnableConfig(tags=["trace", EXAMPLE_ID], metadata={"trace_id":trace_id, "span":"llm"}, callbacks=[rec])
    print("trace_id:", trace_id)
    print(chain.invoke({"q":"用一句话说明 tracing"}, config=cfg))
    print("见 logs ask/answer 与 metadata.trace_id（可对接 OTel/LangSmith）")
if __name__=="__main__":
    main()
