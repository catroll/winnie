"""CLI：演示 Runnable / Lambda / Sequence / Parallel 与标准 Pipeline。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from common.llm_record import LlmInteractionRecorder

from pipeline import (
    build_model,
    build_parallel,
    build_pipe_pipeline,
    build_sequence_pipeline,
    build_with_lambda,
    normalize_question,
)

EXAMPLE_ID = "05langchain-runnable"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"

MODES = ("sequence", "pipe", "lambda", "parallel")

def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        sibling = ROOT.parent / "01langchain-demo" / ".env"
        if sibling.exists():
            load_dotenv(sibling)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY。请复制 .env.example 为 .env。", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="05langchain-runnable playground")
    parser.add_argument(
        "--mode",
        choices=[*MODES, "all"],
        default="all",
        help="演示哪一类 Runnable 组合",
    )
    parser.add_argument(
        "-q",
        "--question",
        default="什么是 LangChain 的 Runnable？",
        help="用户问题",
    )
    args = parser.parse_args(argv)

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")
    print(f"User: {args.question}")
    print(f"normalize → {normalize_question(args.question)}")

    model = build_model()
    config = {"callbacks": [recorder]}
    modes = list(MODES) if args.mode == "all" else [args.mode]

    builders = {
        "sequence": build_sequence_pipeline,
        "pipe": build_pipe_pipeline,
        "lambda": build_with_lambda,
        "parallel": build_parallel,
    }

    for mode in modes:
        print(f"\n========== mode={mode} ==========")
        chain = builders[mode](model)
        # 统一入口：都是 Runnable，都有 invoke
        print(f"runnable type: {type(chain).__name__}")
        if mode in ("sequence", "pipe"):
            # 这两条吃 dict
            out = chain.invoke({"question": args.question}, config=config)
        else:
            # lambda / parallel 入口吃 str
            out = chain.invoke(args.question, config=config)
        if isinstance(out, dict):
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(out)

if __name__ == "__main__":
    main()
