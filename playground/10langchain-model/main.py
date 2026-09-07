"""CLI：ModelFactory 统一调用 OpenAI / DeepSeek / Qwen / Claude。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from common.llm_record import LlmInteractionRecorder

from factory import PROVIDERS, ModelFactory

EXAMPLE_ID = "10langchain-model"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"

def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY") and not any(
        os.getenv(f"{p.upper()}_API_KEY") for p in ("DEEPSEEK", "QWEN", "CLAUDE")
    ):
        sibling = ROOT.parent / "01langchain-demo" / ".env"
        if sibling.exists():
            load_dotenv(sibling)

    parser = argparse.ArgumentParser(description="10langchain-model playground")
    parser.add_argument(
        "--provider",
        choices=[*PROVIDERS, "all"],
        default=None,
        help="指定厂商；默认 DEFAULT_PROVIDER / 第一个已配置项；all=逐个调用已配置厂商",
    )
    parser.add_argument(
        "-q",
        "--question",
        default="用一句话介绍你自己（模型名即可）。",
        help="用户问题",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="只列出 Factory 可见的 provider 配置，不调用模型",
    )
    args = parser.parse_args(argv)

    factory = ModelFactory()
    print("LangChain Interface ← ModelFactory ← Providers\n")
    for p in PROVIDERS:
        info = factory.describe(p)
        mark = "OK" if info["configured"] else "--"
        print(
            f"  [{mark}] {info['provider']:8} model={info['model']}  "
            f"base_url={info['base_url']}"
        )

    if args.list:
        return

    if not factory.available():
        print("\n缺少 API Key。请复制 .env.example 为 .env。", file=sys.stderr)
        sys.exit(1)

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"\n[llm-record] dir → {recorder.dir}")
    print(f"User: {args.question}\n")

    targets = (
        list(factory.available())
        if args.provider == "all"
        else [args.provider or factory._resolve_provider(None)]
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "用一句中文回答。"),
            ("human", "{question}"),
        ]
    )
    config = {"callbacks": [recorder]}

    for name in targets:
        print(f"========== provider={name} ==========")
        model = factory.create(name)
        # 业务侧只认 BaseChatModel / LCEL，不写 if openai / if deepseek
        chain = prompt | model | StrOutputParser()
        answer = chain.invoke({"question": args.question}, config=config)
        print(f"type: {type(model).__name__}")
        print(f"answer: {answer}\n")

if __name__ == "__main__":
    main()
