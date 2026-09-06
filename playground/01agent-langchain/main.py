"""CLI：演示 Agent 基本概念（Model / Tools / Prompt / Messages / Loop）。"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from langchain.messages import AIMessage, HumanMessage, ToolMessage

from agent import build_agent


def _print_trace(messages: list) -> None:
    """打印一轮 invoke 后的消息轨迹，便于观察 Agent 循环。"""
    print("\n—— 消息轨迹（Agent Loop）——")
    for i, msg in enumerate(messages, 1):
        kind = type(msg).__name__
        if isinstance(msg, HumanMessage):
            print(f"{i}. [Human] {msg.content}")
        elif isinstance(msg, AIMessage):
            if msg.tool_calls:
                calls = ", ".join(
                    f"{c['name']}({c.get('args', {})})" for c in msg.tool_calls
                )
                print(f"{i}. [AI → tools] {calls}")
            else:
                print(f"{i}. [AI] {msg.content}")
        elif isinstance(msg, ToolMessage):
            print(f"{i}. [Tool:{msg.name}] {msg.content}")
        else:
            print(f"{i}. [{kind}] {getattr(msg, 'content', msg)}")


def run_once(query: str, *, show_trace: bool = True) -> str:
    agent = build_agent()
    result = agent.invoke({"messages": [{"role": "user", "content": query}]})
    messages = result["messages"]
    if show_trace:
        _print_trace(messages)
    last = messages[-1]
    content = getattr(last, "content", str(last))
    return content if isinstance(content, str) else str(content)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY。请复制 .env.example 为 .env 并填写。", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="01agent-langchain playground")
    parser.add_argument(
        "query",
        nargs="?",
        default="现在几点？再算一下 (17+8)*3，并把偏好 answer_lang 记为 zh。",
        help="发给 Agent 的用户问题",
    )
    parser.add_argument("--no-trace", action="store_true", help="不打印中间 tool 轨迹")
    args = parser.parse_args(argv)

    print(f"User: {args.query}")
    answer = run_once(args.query, show_trace=not args.no_trace)
    print("\n—— 最终回复 ——")
    print(answer)


if __name__ == "__main__":
    main()
