"""CLI：LCEL 管道编排（prompt | model | parser）。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared.llm_record import LlmInteractionRecorder  # noqa: E402

from chains import (
    build_basic_chain,
    build_composed_chain,
    build_from_template_chain,
    build_model,
)

EXAMPLE_ID = "06langchain-lcel"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"

MODES = ("basic", "compose", "swap_prompt")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        sibling = ROOT.parent / "01langchain-demo" / ".env"
        if sibling.exists():
            load_dotenv(sibling)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY。请复制 .env.example 为 .env。", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="06langchain-lcel playground")
    parser.add_argument(
        "--mode",
        choices=[*MODES, "all"],
        default="all",
        help="basic=标准管道；compose=再组合；swap_prompt=换 Prompt 复用 Model/Parser",
    )
    parser.add_argument(
        "-q",
        "--question",
        default="什么是 LCEL？",
        help="用户问题",
    )
    args = parser.parse_args(argv)

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")
    print(f"User: {args.question}")

    model = build_model()
    config = {"callbacks": [recorder]}
    builders = {
        "basic": build_basic_chain,
        "compose": build_composed_chain,
        "swap_prompt": build_from_template_chain,
    }
    modes = list(MODES) if args.mode == "all" else [args.mode]

    for mode in modes:
        print(f"\n========== mode={mode} ==========")
        chain = builders[mode](model)
        print(f"chain type: {type(chain).__name__}")
        # 声明式管道：一处 invoke，数据按 | 流向下游
        print("shape: prompt | model | parser" + (" | lambda" if mode == "compose" else ""))
        out = chain.invoke({"question": args.question}, config=config)
        print(out)


if __name__ == "__main__":
    main()
