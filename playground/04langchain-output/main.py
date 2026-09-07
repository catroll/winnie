"""多路径演示：Structured Output / Pydantic / JSON Schema / Output Parser。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from common.llm_record import LlmInteractionRecorder

from schema import DEFAULT_NEWS, NEWS_JSON_SCHEMA, NewsAnalysis

EXAMPLE_ID = "04langchain-output"
ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"

MODES = ("pydantic", "json_schema", "parser")

# 部分兼容网关不支持 response_format=json_schema / tool_choice；
# json_mode 更通用，但 Prompt 里必须出现 json，并写清字段合约。
_STRUCTURED_SYSTEM = (
    "分析用户给出的新闻，只输出一个 json 对象（不要 Markdown 代码块）。"
    "字段必须且仅为：title(string), summary(string), keywords(string[]), "
    "sentiment(positive|neutral|negative)。"
)

def build_model():
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    kwargs: dict = {"temperature": 0}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key
    return init_chat_model(f"openai:{model}", **kwargs)

def _print_result(mode: str, data: NewsAnalysis | dict) -> None:
    print(f"\n—— 结构化结果 ({mode}) ——")
    if isinstance(data, NewsAnalysis):
        payload = data.model_dump()
        print(f"type: {type(data).__name__}  ← 可直接给程序用的对象")
    else:
        payload = data
        print(f"type: {type(data).__name__}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

def run_pydantic(model, news: str, config: dict) -> NewsAnalysis:
    """推荐概念：Pydantic 合约 + with_structured_output → 模型实例。"""
    structured = model.with_structured_output(NewsAnalysis, method="json_mode")
    messages = [
        SystemMessage(content=_STRUCTURED_SYSTEM),
        HumanMessage(content=f"分析这段新闻：\n\n{news}"),
    ]
    result = structured.invoke(messages, config=config)
    if isinstance(result, dict):
        result = NewsAnalysis.model_validate(result)
    assert isinstance(result, NewsAnalysis)
    return result

def run_json_schema(model, news: str, config: dict) -> dict:
    """JSON Schema 合约：不依赖业务侧 import 某个 BaseModel 类名。"""
    structured = model.with_structured_output(NEWS_JSON_SCHEMA, method="json_mode")
    schema_hint = json.dumps(NEWS_JSON_SCHEMA, ensure_ascii=False)
    messages = [
        SystemMessage(
            content=_STRUCTURED_SYSTEM + f"\nJSON Schema:\n{schema_hint}"
        ),
        HumanMessage(content=f"分析这段新闻：\n\n{news}"),
    ]
    result = structured.invoke(messages, config=config)
    if isinstance(result, NewsAnalysis):
        return result.model_dump()
    return dict(result)

def run_parser(model, news: str, config: dict) -> NewsAnalysis:
    """经典 Output Parser：模型先吐 JSON 文本，再解析校验为 Pydantic。"""
    parser = PydanticOutputParser(pydantic_object=NewsAnalysis)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "分析新闻并只输出符合格式的 json（不要代码块）。\n{format_instructions}",
            ),
            ("human", "分析这段新闻：\n\n{news}"),
        ]
    )
    messages = prompt.format_messages(
        news=news,
        format_instructions=parser.get_format_instructions(),
    )
    ai = model.invoke(messages, config=config)
    return parser.parse(getattr(ai, "content", str(ai)))

RUNNERS = {
    "pydantic": run_pydantic,
    "json_schema": run_json_schema,
    "parser": run_parser,
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

    parser = argparse.ArgumentParser(description="04langchain-output playground")
    parser.add_argument(
        "--mode",
        choices=[*MODES, "all"],
        default="all",
        help="结构化输出路径（默认 all）",
    )
    parser.add_argument(
        "--news",
        default=DEFAULT_NEWS,
        help="待分析新闻文本",
    )
    args = parser.parse_args(argv)

    recorder = LlmInteractionRecorder(LOGS_DIR, example_id=EXAMPLE_ID)
    print(f"[llm-record] dir → {recorder.dir}")
    print(f"User: 分析这段新闻\n\n{args.news}\n")

    model = build_model()
    config = {"callbacks": [recorder]}
    modes = list(MODES) if args.mode == "all" else [args.mode]

    for mode in modes:
        print(f"\n========== mode={mode} ==========")
        result = RUNNERS[mode](model, args.news, config)
        _print_result(mode, result)

if __name__ == "__main__":
    main()
