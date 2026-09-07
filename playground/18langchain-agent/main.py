"""CLI：第一个 Agent — create_agent / Loop / State(messages)。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from langchain.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))

from _shared.llm_record import LlmInteractionRecorder
from _shared.model import load_env
from mini_agent import build_loop_agent, build_plain_agent

EXAMPLE_ID = "18langchain-agent"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"


def _print_state(messages: list) -> None:
    print("—— Agent State (messages) ——")
    for i, msg in enumerate(messages, 1):
        if isinstance(msg, HumanMessage):
            print(f"{i}. Human: {msg.content}")
        elif isinstance(msg, AIMessage):
            if msg.tool_calls:
                print(f"{i}. AI → tools: {[c['name'] for c in msg.tool_calls]}")
            else:
                print(f"{i}. AI: {msg.content}")
        elif isinstance(msg, ToolMessage):
            print(f"{i}. Tool[{msg.name}]: {msg.content}")
        else:
            print(f"{i}. {type(msg).__name__}: {getattr(msg, 'content', msg)}")


def main(argv=None) -> None:
    load_env(ROOT)
    import os

    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["plain", "loop", "all"], default="all")
    parser.add_argument("-q", default="用一句话说明什么是 Agent。")
    args = parser.parse_args(argv)

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")
    print("User → Agent → LLM → Answer\n")
    config = {"callbacks": [recorder]}
    modes = ["plain", "loop"] if args.mode == "all" else [args.mode]

    for mode in modes:
        print(f"========== mode={mode} ==========")
        if mode == "plain":
            agent = build_plain_agent()
            q = args.q
        else:
            agent = build_loop_agent()
            q = "现在大概什么时间？（可用工具）"
        print(f"User: {q}")
        result = agent.invoke({"messages": [{"role": "user", "content": q}]}, config=config)
        _print_state(result["messages"])
        print(f"graph type: {type(agent).__name__}")
        print()


if __name__ == "__main__":
    main()
