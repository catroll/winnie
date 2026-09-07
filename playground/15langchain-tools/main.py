"""CLI：多工具 Agent — Selection / Calling / Result。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from langchain.agents import create_agent
from langchain.messages import AIMessage, ToolMessage

from common.llm_record import LlmInteractionRecorder
from common.model import build_chat_model, load_env
from tools_kit import TOOLS

EXAMPLE_ID = "15langchain-tools"
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
        default="查北京天气，并算 (3+5)*2，再查订单 order-1001 状态。",
    )
    args = parser.parse_args(argv)

    print("Agent\n ├── Weather\n ├── Search\n ├── Calculator\n └── Database\n")
    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")

    agent = create_agent(
        model=build_chat_model(),
        tools=TOOLS,
        system_prompt="按需选择工具；可多工具；最后用中文汇总。",
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": args.q}]},
        config={"callbacks": [recorder]},
    )

    print(f"User: {args.q}\n—— 轨迹 ——")
    for msg in result["messages"]:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for c in msg.tool_calls:
                print(f"  tool_call: {c['name']}({c.get('args')})")
        elif isinstance(msg, ToolMessage):
            print(f"  tool_result[{msg.name}]: {msg.content}")
        elif isinstance(msg, AIMessage):
            print(f"  ai: {msg.content}")
    print(f"\nFinal: {result['messages'][-1].content}")

if __name__ == "__main__":
    main()
