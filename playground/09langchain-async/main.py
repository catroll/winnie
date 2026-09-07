"""CLI：ainvoke / gather / 限流 / 超时 / astream。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from common.llm_record import LlmInteractionRecorder

from async_demo import (
    DEFAULT_TASKS,
    build_chain,
    build_model,
    run_astream_one,
    run_gather,
    run_limited,
    run_serial,
    run_with_timeout,
    timed,
)

EXAMPLE_ID = "09langchain-async"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"

MODES = ("serial", "gather", "limit", "timeout", "astream")

async def _run(mode: str, questions: list[str], config: dict) -> None:
    chain = build_chain(build_model())

    if mode == "serial":
        print("Task1 → Task2 → Task3  (串行 ainvoke)")
        label, elapsed, results = await timed(
            "serial", run_serial(chain, questions, config)
        )
    elif mode == "gather":
        print("Task1 ─┐\nTask2 ─┼── asyncio.gather()\nTask3 ─┘")
        label, elapsed, results = await timed(
            "gather", run_gather(chain, questions, config)
        )
    elif mode == "limit":
        print("gather + Semaphore(2) 限流")
        label, elapsed, results = await timed(
            "limit", run_limited(chain, questions, config, limit=2)
        )
    elif mode == "timeout":
        print("asyncio.wait_for(ainvoke, timeout=30)")
        label, elapsed, results = await timed(
            "timeout",
            run_with_timeout(chain, questions[0], config, timeout=30.0),
        )
        results = [results]
    else:
        print("astream（与第 8 章衔接）")
        print("—— 流式 ——")
        label, elapsed, text = await timed(
            "astream", run_astream_one(chain, questions[0], config)
        )
        results = [text]

    print(f"\nelapsed[{label}]: {elapsed:.2f}s")
    for i, (q, a) in enumerate(zip(questions, results, strict=False), 1):
        print(f"\n[{i}] Q: {q}\n    A: {a}")

def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        sibling = ROOT.parent / "01langchain-demo" / ".env"
        if sibling.exists():
            load_dotenv(sibling)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY。请复制 .env.example 为 .env。", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="09langchain-async playground")
    parser.add_argument(
        "--mode",
        choices=[*MODES, "compare", "all"],
        default="compare",
        help="compare=串行 vs gather 耗时对比；all=全部模式",
    )
    parser.add_argument(
        "-q",
        action="append",
        dest="questions",
        help="自定义任务（可重复）；默认三道短问",
    )
    args = parser.parse_args(argv)
    questions = args.questions or list(DEFAULT_TASKS)

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")
    config = {"callbacks": [recorder]}

    async def runner() -> None:
        if args.mode == "compare":
            for m in ("serial", "gather"):
                print(f"\n========== mode={m} ==========")
                await _run(m, questions, config)
            print(
                "\n建议对比：gather 耗时应明显小于 serial（接近最慢任务，而非三者之和）。"
            )
        elif args.mode == "all":
            for m in MODES:
                print(f"\n========== mode={m} ==========")
                await _run(m, questions, config)
        else:
            print(f"\n========== mode={args.mode} ==========")
            await _run(args.mode, questions, config)

    asyncio.run(runner())

if __name__ == "__main__":
    main()
