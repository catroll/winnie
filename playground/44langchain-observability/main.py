"""44langchain-observability：用 Callback 采集 calls / latency / tokens。"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.output_parsers import StrOutputParser
from langchain_core.outputs import LLMResult
from langchain_core.prompts import ChatPromptTemplate

ROOT = Path(__file__).resolve().parent
from common.llm_record import LlmInteractionRecorder
from common.model import build_chat_model, load_env

EXAMPLE_ID = ROOT.name
LOGS_DIR = ROOT / "logs"

@dataclass
class Metrics:
    calls: int = 0
    errors: int = 0
    tokens: int = 0
    latency_ms: list[float] = field(default_factory=list)

class MetricsHandler(BaseCallbackHandler):
    def __init__(self, m: Metrics) -> None:
        super().__init__()
        self.m = m
        self._t: dict[UUID, float] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id: UUID, **kwargs):
        self._t[run_id] = time.perf_counter()

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs):
        self.m.calls += 1
        if run_id in self._t:
            self.m.latency_ms.append((time.perf_counter() - self._t.pop(run_id)) * 1000)
        usage = (response.llm_output or {}).get("token_usage") or {}
        self.m.tokens += usage.get("total_tokens") or 0

    def on_llm_error(self, error, *, run_id: UUID, **kwargs):
        self.m.errors += 1

def main() -> None:
    load_env(ROOT)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)

    m = Metrics()
    handler = MetricsHandler(m)
    rec = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    chain = (
        ChatPromptTemplate.from_messages([("human", "{q}")])
        | build_chat_model()
        | StrOutputParser()
    )
    for q in ["QPS 是什么", "Latency 是什么", "Token 是什么"]:
        chain.invoke({"q": q}, config={"callbacks": [handler, rec]})

    avg = round(sum(m.latency_ms) / len(m.latency_ms), 2) if m.latency_ms else None
    print(
        json.dumps(
            {
                "calls": m.calls,
                "errors": m.errors,
                "tokens": m.tokens,
                "avg_latency_ms": avg,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

if __name__ == "__main__":
    main()
