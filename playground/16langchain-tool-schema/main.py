"""CLI：JSON Arguments → Pydantic Validation → Python Function。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from langchain.agents import create_agent
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))

from _shared.llm_record import LlmInteractionRecorder
from _shared.model import build_chat_model, load_env
from schema_tools import SearchInput, catalog_add, search

EXAMPLE_ID = "16langchain-tool-schema"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"


def main(argv=None) -> None:
    load_env(ROOT)
    import os

    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["schema", "validate", "agent", "all"],
        default="all",
    )
    parser.add_argument("-q", default="搜索 LangChain，只要 2 条，按时间排序。")
    args = parser.parse_args(argv)

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    modes = ["schema", "validate", "agent"] if args.mode == "all" else [args.mode]

    for mode in modes:
        print(f"\n========== mode={mode} ==========")
        if mode == "schema":
            print("SearchInput JSON Schema:")
            print(json.dumps(SearchInput.model_json_schema(), ensure_ascii=False, indent=2))
        elif mode == "validate":
            print("合法参数:")
            print(SearchInput(keyword="RAG", limit=2, sort="time"))
            print("非法参数 (limit=99):")
            try:
                SearchInput(keyword="RAG", limit=99)
            except ValidationError as exc:
                print(exc)
        else:
            print("LLM → JSON args → Pydantic → Function")
            print(f"[llm-record] dir → {recorder.dir}")
            agent = create_agent(
                model=build_chat_model(),
                tools=[search, catalog_add],
                system_prompt="严格按工具 schema 传参；中文简短回复。",
            )
            result = agent.invoke(
                {"messages": [{"role": "user", "content": args.q}]},
                config={"callbacks": [recorder]},
            )
            print(f"User: {args.q}")
            print(f"Assistant: {result['messages'][-1].content}")


if __name__ == "__main__":
    main()
