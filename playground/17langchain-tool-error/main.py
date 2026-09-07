"""CLI：Tool Error → Error Message → LLM Retry → New Arguments。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from langchain.agents import create_agent
from langchain.messages import AIMessage, ToolMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))

from _shared.llm_record import LlmInteractionRecorder
from _shared.model import build_chat_model, load_env
from err_tools import echo_ok, lookup_city_code

EXAMPLE_ID = "17langchain-tool-error"
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
        "-q",
        default="查一下「广州」的城市编码；如果失败就换北京再查。",
        help="诱导先错后对的问题",
    )
    args = parser.parse_args(argv)

    print("Tool Error → Message → LLM Retry → New Args\n")
    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")

    agent = create_agent(
        model=build_chat_model(),
        tools=[lookup_city_code, echo_ok],
        system_prompt=(
            "调用 lookup_city_code 时若工具报错，阅读错误信息，"
            "换合法城市参数重试，再回答用户。中文简短。"
        ),
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": args.q}]},
        config={"callbacks": [recorder]},
    )

    print(f"User: {args.q}\n—— 轨迹 ——")
    for msg in result["messages"]:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for c in msg.tool_calls:
                print(f"  call: {c['name']}({c.get('args')})")
        elif isinstance(msg, ToolMessage):
            status = "ERROR?" if "Error" in str(msg.content) or "unsupported" in str(msg.content) else "ok"
            print(f"  result[{status}] {msg.name}: {msg.content}")
        elif isinstance(msg, AIMessage) and msg.content:
            print(f"  ai: {msg.content}")
    print(f"\nFinal: {result['messages'][-1].content}")


if __name__ == "__main__":
    main()
