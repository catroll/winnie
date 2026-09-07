"""CLI：Callback 驱动的 AI 调用日志（成功 / 工具 / 错误）。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain.agents import create_agent

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared.llm_record import LlmInteractionRecorder  # noqa: E402

from logging_cb import AiCallLogHandler

EXAMPLE_ID = "12langchain-callback"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"


def build_model():
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    kwargs: dict = {"temperature": 0}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key
    return init_chat_model(f"openai:{model}", **kwargs)


@tool
def ping(service: str) -> str:
    """探测服务是否可达；传入 service 名称。"""
    return f"{service}: ok"


@tool
def fail_tool(reason: str) -> str:
    """故意失败的工具，用于演示 Error 回调。"""
    raise RuntimeError(f"simulated tool failure: {reason}")


def run_ok(question: str, config: dict) -> None:
    chain = (
        ChatPromptTemplate.from_messages(
            [
                ("system", "用一两句中文回答。"),
                ("human", "{question}"),
            ]
        )
        | build_model()
        | StrOutputParser()
    )
    print("mode=ok → 普通 LLM 调用")
    answer = chain.invoke({"question": question}, config=config)
    print(f"Assistant: {answer}")


def run_tool(question: str, config: dict) -> None:
    print("mode=tool → Agent + Tool Start")
    agent = create_agent(
        model=build_model(),
        tools=[ping],
        system_prompt="需要时调用 ping 工具；最后用中文简短回复。",
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config=config,
    )
    last = result["messages"][-1]
    print(f"Assistant: {getattr(last, 'content', last)}")


def run_error(config: dict) -> None:
    print("mode=error → 无效模型名触发 LLM Error")
    # 构造必失败的模型调用
    bad = init_chat_model(
        "openai:this-model-does-not-exist-winnie",
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        temperature=0,
    )
    try:
        bad.invoke("ping", config=config)
    except Exception as exc:  # noqa: BLE001
        print(f"(caught) {type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        sibling = ROOT.parent / "01langchain-demo" / ".env"
        if sibling.exists():
            load_dotenv(sibling)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY。请复制 .env.example 为 .env。", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="12langchain-callback playground")
    parser.add_argument(
        "--mode",
        choices=["ok", "tool", "error", "all"],
        default="all",
        help="ok=成功日志；tool=Tool Start；error=LLM Error",
    )
    parser.add_argument(
        "-q",
        "--question",
        default="用一句话说明什么是 Callback。",
        help="用户问题（ok 模式）",
    )
    args = parser.parse_args(argv)

    ai_log = AiCallLogHandler(LOGS_DIR / "ai-calls")
    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")
    print(f"[ai-calls]   dir → {ai_log.log_dir}")

    config = {"callbacks": [ai_log, recorder], "tags": ["12langchain-callback"]}
    modes = ["ok", "tool", "error"] if args.mode == "all" else [args.mode]

    for mode in modes:
        print(f"\n========== mode={mode} ==========")
        if mode == "ok":
            run_ok(args.question, config)
        elif mode == "tool":
            run_tool("请用 ping 工具探测 redis。", config)
        else:
            run_error(config)

    print(f"\n共记录 {len(ai_log.records)} 条 AI 调用日志。")


if __name__ == "__main__":
    main()
