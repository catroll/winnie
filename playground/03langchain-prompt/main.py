"""CLI：PromptTemplate / ChatPromptTemplate / Few-shot / 动态 Prompt 演示。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage

from common.llm_record import LlmInteractionRecorder

from prompts import (
    DYNAMIC_CHAT,
    FEWSHOT_CHAT,
    PLAIN_STRING,
    VARIABLE_CHAT,
    build_model,
    demo_history,
)

EXAMPLE_ID = "03langchain-prompt"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"

MODES = ("plain", "variable", "fewshot", "dynamic")

def _show_messages(label: str, messages: list[BaseMessage]) -> None:
    print(f"\n—— {label}：填充后的 Messages ——")
    for i, msg in enumerate(messages, 1):
        role = type(msg).__name__
        content = getattr(msg, "content", "")
        print(f"{i}. [{role}] {content}")

def _invoke_chat(model, messages: list[BaseMessage], config: dict) -> str:
    ai = model.invoke(messages, config=config)
    text = getattr(ai, "content", str(ai))
    return text if isinstance(text, str) else str(text)

def run_plain(model, question: str, config: dict) -> str:
    """普通：PromptTemplate → 字符串 → 包成 Human 再调模型（对比用）。"""
    # 字符串模板只做变量填充；Chat 模型仍吃 Message
    text = PLAIN_STRING.format(concept=question)
    from langchain_core.messages import HumanMessage

    messages = [HumanMessage(content=text)]
    _show_messages("plain (PromptTemplate)", messages)
    return _invoke_chat(model, messages, config)

def run_variable(model, question: str, config: dict) -> str:
    """变量：ChatPromptTemplate + 多槽位；partial 可预填。"""
    prompt = VARIABLE_CHAT.partial(role="技术助教", tone="简洁直接")
    messages = prompt.format_messages(topic="LangChain Prompt", question=question)
    _show_messages("variable (ChatPromptTemplate + partial)", messages)
    return _invoke_chat(model, messages, config)

def run_fewshot(model, question: str, config: dict) -> str:
    """Few-shot：样例消息嵌入模板后再接用户输入。"""
    messages = FEWSHOT_CHAT.format_messages(input=question)
    _show_messages("fewshot (FewShotChatMessagePromptTemplate)", messages)
    return _invoke_chat(model, messages, config)

def run_dynamic(model, question: str, config: dict) -> str:
    """动态：MessagesPlaceholder 注入 history；product 已 partial。"""
    messages = DYNAMIC_CHAT.format_messages(
        history=demo_history(),
        question=question,
    )
    _show_messages("dynamic (MessagesPlaceholder + partial)", messages)
    return _invoke_chat(model, messages, config)

RUNNERS = {
    "plain": run_plain,
    "variable": run_variable,
    "fewshot": run_fewshot,
    "dynamic": run_dynamic,
}

DEFAULT_QUESTIONS = {
    "plain": "PromptTemplate",
    "variable": "ChatPromptTemplate 和 PromptTemplate 有什么差别？",
    "fewshot": "用户说：回答请一律用中文，先给结论。",
    "dynamic": "我们选的向量库是什么？",
}

def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        sibling = ROOT.parent / "01langchain-demo" / ".env"
        if sibling.exists():
            load_dotenv(sibling)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY。请复制 .env.example 为 .env。", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="03langchain-prompt playground")
    parser.add_argument(
        "--mode",
        choices=[*MODES, "all"],
        default="all",
        help="演示哪一类 Prompt（默认 all）",
    )
    parser.add_argument(
        "-q",
        "--question",
        help="用户问题；省略则用各 mode 的默认示例句",
    )
    args = parser.parse_args(argv)

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")
    model = build_model()
    config = {"callbacks": [recorder]}

    modes = list(MODES) if args.mode == "all" else [args.mode]
    for mode in modes:
        question = args.question or DEFAULT_QUESTIONS[mode]
        print(f"\n========== mode={mode} ==========")
        print(f"User: {question}")
        answer = RUNNERS[mode](model, question, config)
        print(f"\nAssistant:\n{answer}")

if __name__ == "__main__":
    main()
