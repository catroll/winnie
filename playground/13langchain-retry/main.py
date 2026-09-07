"""CLI：Retry / Timeout / Fallback。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))

from _shared.llm_record import LlmInteractionRecorder
from _shared.model import load_env

from retry_demo import build_fallback_chain, build_primary_chain, build_retry_lambda

EXAMPLE_ID = "13langchain-retry"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"


def main(argv: list[str] | None = None) -> None:
    load_env(ROOT)
    import os

    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["retry", "fallback", "timeout", "all"], default="all")
    parser.add_argument("-q", default="什么是 Fallback？")
    args = parser.parse_args(argv)

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")
    config = {"callbacks": [recorder]}
    modes = ["retry", "fallback", "timeout"] if args.mode == "all" else [args.mode]

    for mode in modes:
        print(f"\n========== mode={mode} ==========")
        if mode == "retry":
            print("LLM A 失败 → Retry → … → 成功（模拟 ConnectionError）")
            chain = build_retry_lambda(fail_times=2)
            print(chain.invoke("ping"))
        elif mode == "fallback":
            print("无效模型失败 → Fallback → 可用模型")
            chain = build_fallback_chain()
            print(chain.invoke({"question": args.q}, config=config))
        else:
            print("asyncio.wait_for 超时保护（正常调用应在时限内完成）")
            chain = build_primary_chain()

            async def _run():
                return await asyncio.wait_for(
                    chain.ainvoke({"question": args.q}, config=config),
                    timeout=30.0,
                )

            print(asyncio.run(_run()))


if __name__ == "__main__":
    main()
