"""CLI：stream / astream 演示；Token 经 Callback 打印。可对接 CLI / SSE / WebSocket。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from common.llm_record import LlmInteractionRecorder

from streamer import TokenPrintHandler, build_chain, build_model

EXAMPLE_ID = "08langchain-stream"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"

def run_stream(question: str, config: dict) -> str:
    """同步 stream()：按块迭代，适合 CLI。"""
    chain = build_chain(build_model())
    print("\n—— stream() 输出（边到边打）——\n")
    parts: list[str] = []
    for chunk in chain.stream({"question": question}, config=config):
        parts.append(chunk)
        # CLI 实时刷新；Web 可改为 SSE data: 行
        print(chunk, end="", flush=True)
    print("\n")
    return "".join(parts)

async def run_astream(question: str, config: dict) -> str:
    """异步 astream()：同样的流，挂到 asyncio（Web/ASGI 常用）。"""
    chain = build_chain(build_model())
    print("\n—— astream() 输出 ——\n")
    parts: list[str] = []
    async for chunk in chain.astream({"question": question}, config=config):
        parts.append(chunk)
        print(chunk, end="", flush=True)
    print("\n")
    return "".join(parts)

def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        sibling = ROOT.parent / "01langchain-demo" / ".env"
        if sibling.exists():
            load_dotenv(sibling)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY。请复制 .env.example 为 .env。", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="08langchain-stream playground")
    parser.add_argument(
        "--mode",
        choices=["stream", "astream", "callback", "all"],
        default="all",
        help="stream=同步；astream=异步；callback=看 on_llm_new_token",
    )
    parser.add_argument(
        "-q",
        "--question",
        default="用几句话介绍什么是 Token Streaming，以及它为何适合聊天 UI。",
        help="用户问题",
    )
    args = parser.parse_args(argv)

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")
    print(f"User: {args.question}")
    print(
        "下游可接: CLI（本 Demo）/ Web / SSE / WebSocket —— 差别只在「chunk 往哪写」。"
    )

    modes = ["stream", "astream", "callback"] if args.mode == "all" else [args.mode]

    for mode in modes:
        print(f"\n========== mode={mode} ==========")
        if mode == "stream":
            config = {"callbacks": [recorder]}
            text = run_stream(args.question, config)
            print(f"(assembled length: {len(text)})")
        elif mode == "astream":
            config = {"callbacks": [recorder]}
            text = asyncio.run(run_astream(args.question, config))
            print(f"(assembled length: {len(text)})")
        else:
            token_cb = TokenPrintHandler()
            # 直接对流式模型：Callback 收 token；chain.stream 仍可同时打全文
            config = {"callbacks": [recorder, token_cb]}
            print("\n—— Callback TokenN ——\n")
            chain = build_chain(build_model())
            # 需 streaming 才会触发 on_llm_new_token
            for _ in chain.stream({"question": args.question}, config=config):
                pass
            print("\n—— 拼接全文 ——\n")
            print("".join(token_cb.tokens))

if __name__ == "__main__":
    main()
