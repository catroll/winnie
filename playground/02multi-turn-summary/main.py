"""CLI：多轮会话保留上下文，最后一轮总结输出。"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain.messages import AIMessage, HumanMessage, SystemMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared.llm_record import LlmInteractionRecorder  # noqa: E402

from conversation import DEMO_TURNS, SUMMARY_ASK, build_model, initial_messages

EXAMPLE_ID = "02multi-turn-summary"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"


def _content(msg) -> str:
    c = getattr(msg, "content", str(msg))
    return c if isinstance(c, str) else str(c)


def _print_history(messages: list) -> None:
    print("\n—— 当前上下文（Messages）——")
    for i, msg in enumerate(messages, 1):
        if isinstance(msg, SystemMessage):
            preview = _content(msg).replace("\n", " ")[:60]
            print(f"{i}. [System] {preview}...")
        elif isinstance(msg, HumanMessage):
            print(f"{i}. [Human] {_content(msg)}")
        elif isinstance(msg, AIMessage):
            print(f"{i}. [AI] {_content(msg)}")
        else:
            print(f"{i}. [{type(msg).__name__}] {_content(msg)}")


def run_conversation(turns: list[str], *, show_context: bool = True) -> str:
    """逐轮追加 Human/AI，整段 messages 作为上下文；最后返回总结正文。"""
    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")

    model = build_model()
    messages = initial_messages()
    config = {"callbacks": [recorder]}

    for idx, user_text in enumerate(turns, 1):
        print(f"\n===== 第 {idx}/{len(turns)} 轮 =====")
        print(f"User: {user_text}")
        messages.append(HumanMessage(content=user_text))
        ai = model.invoke(messages, config=config)
        messages.append(ai)
        print(f"Assistant: {_content(ai)}")
        if show_context:
            _print_history(messages)

    print("\n===== 总结轮 =====")
    print(f"User: {SUMMARY_ASK}")
    messages.append(HumanMessage(content=SUMMARY_ASK))
    summary_msg = model.invoke(messages, config=config)
    messages.append(summary_msg)
    summary = _content(summary_msg)

    out = LOGS_DIR / f"summary-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(summary.rstrip() + "\n", encoding="utf-8")
    print(f"[summary] → {out}")
    if show_context:
        _print_history(messages)
    return summary


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    # 允许复用隔壁示例的 .env
    if not os.getenv("OPENAI_API_KEY"):
        sibling = ROOT.parent / "01agent-langchain" / ".env"
        if sibling.exists():
            load_dotenv(sibling)

    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY。请复制 .env.example 为 .env 并填写。", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="02multi-turn-summary playground")
    parser.add_argument(
        "--turn",
        action="append",
        dest="turns",
        help="自定义一轮用户发言（可重复）；省略则用内置 DEMO_TURNS",
    )
    parser.add_argument("--no-context", action="store_true", help="不打印累积 Messages")
    args = parser.parse_args(argv)

    turns = args.turns if args.turns else list(DEMO_TURNS)
    summary = run_conversation(turns, show_context=not args.no_context)
    print("\n—— 最终总结 ——")
    print(summary)


if __name__ == "__main__":
    main()
