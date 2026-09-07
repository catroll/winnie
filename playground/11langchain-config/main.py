"""CLI：dev / test / prod 通过 RunnableConfig 动态切换模型。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared.llm_record import LlmInteractionRecorder  # noqa: E402

from config_demo import build_chain, config_for_env, describe_envs

EXAMPLE_ID = "11langchain-config"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"


class ConfigProbeHandler(BaseCallbackHandler):
    """从回调里读出 tags / metadata，证明 Config 已注入运行时。"""

    def __init__(self) -> None:
        super().__init__()
        self.last_tags: list[str] | None = None
        self.last_metadata: dict[str, Any] | None = None

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.last_tags = tags
        self.last_metadata = metadata
        print(f"[config] tags={tags}")
        print(f"[config] metadata={json.dumps(metadata or {}, ensure_ascii=False)}")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        sibling = ROOT.parent / "01langchain-demo" / ".env"
        if sibling.exists():
            load_dotenv(sibling)
    if not os.getenv("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY。请复制 .env.example 为 .env。", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="11langchain-config playground")
    parser.add_argument(
        "--env",
        choices=["dev", "test", "prod", "all"],
        default="all",
        help="目标环境（决定 configurable.model_name）",
    )
    parser.add_argument(
        "-q",
        "--question",
        default="现在是哪个环境在回答？",
        help="用户问题",
    )
    args = parser.parse_args(argv)

    print("Environment → model mapping:")
    for row in describe_envs():
        print(f"  {row['env']:4} → {row['model']}")

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    probe = ConfigProbeHandler()
    print(f"\n[llm-record] dir → {recorder.dir}")
    print(f"User: {args.question}\n")

    chain = build_chain()
    envs = ["dev", "test", "prod"] if args.env == "all" else [args.env]

    for env in envs:
        print(f"========== env={env} ==========")
        cfg = config_for_env(env, question=args.question)  # type: ignore[arg-type]
        # 合并 callbacks：落盘 + 打印 tags/metadata
        cfg = {**cfg, "callbacks": [recorder, probe]}
        print(f"[config] configurable={cfg['configurable']}")
        result = chain.invoke({"question": args.question}, config=cfg)
        print(f"answer: {result.get('answer', result)}\n")


if __name__ == "__main__":
    main()
