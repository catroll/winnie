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

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from uuid import UUID

class CostHandler(BaseCallbackHandler):
    def __init__(self, pin=0.15, pout=0.6):
        self.pin=pin; self.pout=pout; self.total=0.0; self.details=[]
    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs):
        u=(response.llm_output or {}).get("token_usage") or {}
        pt=u.get("prompt_tokens") or 0; ct=u.get("completion_tokens") or 0
        cost=(pt/1e6)*self.pin+(ct/1e6)*self.pout
        self.total+=cost
        self.details.append({"prompt_tokens":pt,"completion_tokens":ct,"cost_usd":round(cost,8)})

def main():
    _ready()
    cost=CostHandler(); rec=LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    chain=ChatPromptTemplate.from_messages([("human","{q}")])|build_chat_model()|StrOutputParser()
    for q in ["你好","解释 token","解释 cost"]:
        chain.invoke({"q":q}, config={"callbacks":[cost, rec]})
    print(json.dumps({"calls":cost.details, "total_usd_estimate":round(cost.total,8)}, ensure_ascii=False, indent=2))
if __name__=="__main__":
    main()
