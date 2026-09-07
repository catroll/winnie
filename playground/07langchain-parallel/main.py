"""CLI：RunnableParallel 四路并行分析文章。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from common.llm_record import LlmInteractionRecorder

from pipeline import DEFAULT_ARTICLE, build_pipeline

EXAMPLE_ID = "07langchain-parallel"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"

def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        sibling = ROOT.parent / "01langchain-demo" / ".env"
        if sibling.exists():
            load_dotenv(sibling)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY。请复制 .env.example 为 .env。", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="07langchain-parallel playground")
    parser.add_argument(
        "--article",
        default=DEFAULT_ARTICLE,
        help="待分析文章",
    )
    parser.add_argument(
        "--multi-model",
        action="store_true",
        help="关键词/情感走 OPENAI_MODEL_B（未设则回退同一模型）",
    )
    args = parser.parse_args(argv)

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")
    print("Pipeline:\n  文章 → Parallel(title, summary, keywords, sentiment) → JSON\n")
    print(f"User article:\n{args.article}\n")

    chain = build_pipeline(multi_model=args.multi_model)
    config = {"callbacks": [recorder]}

    t0 = time.perf_counter()
    result = chain.invoke(args.article, config=config)
    elapsed = time.perf_counter() - t0

    print("—— 并行合并结果 ——")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nelapsed: {elapsed:.2f}s  (四路应接近「最慢一路」而非四路之和)")
    print(f"llm calls recorded under: {recorder.dir}")

if __name__ == "__main__":
    main()
