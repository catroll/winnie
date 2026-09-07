"""CLI：Python 函数 → Tool Schema → Function Calling。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))

from langchain.agents import create_agent

from _shared.llm_record import LlmInteractionRecorder
from _shared.model import build_chat_model, load_env

from tools_demo import get_weather

EXAMPLE_ID = "14langchain-tool"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"


def main(argv=None) -> None:
    load_env(ROOT)
    import os

    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("-q", default="北京今天天气怎么样？")
    parser.add_argument("--schema-only", action="store_true", help="只打印 Tool Schema")
    args = parser.parse_args(argv)

    print("Python Function → Tool Schema → LLM Function Calling\n")
    schema = get_weather.tool_json_schema() if hasattr(get_weather, "tool_json_schema") else None
    # langchain tool exposes .name / .description / args_schema
    print(f"name: {get_weather.name}")
    print(f"description: {get_weather.description}")
    if get_weather.args_schema:
        print("args_schema:")
        print(json.dumps(get_weather.args_schema.model_json_schema(), ensure_ascii=False, indent=2))
    if args.schema_only:
        return

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"\n[llm-record] dir → {recorder.dir}")
    agent = create_agent(
        model=build_chat_model(),
        tools=[get_weather],
        system_prompt="需要天气时调用 get_weather；用中文简短回答。",
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": args.q}]},
        config={"callbacks": [recorder]},
    )
    print(f"\nUser: {args.q}")
    print(f"Assistant: {result['messages'][-1].content}")


if __name__ == "__main__":
    main()
