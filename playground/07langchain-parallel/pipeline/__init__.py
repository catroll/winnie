"""企业风 Pipeline：一文四路并行 → 合并为结构化结果。"""

from __future__ import annotations

import os
from typing import Any, Literal

from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnableParallel
from pydantic import BaseModel, Field

class ArticleAnalysis(BaseModel):
    title: str = Field(description="标题，不超过 20 字")
    summary: str = Field(description="两到三句中文摘要")
    keywords: list[str] = Field(description="3～5 个关键词")
    sentiment: Literal["positive", "neutral", "negative"]

DEFAULT_ARTICLE = """
开源向量数据库 Qdrant 发布新版本，强化过滤查询与单机部署体验。
不少个人开发者计划将其用于知识库检索；也有人提醒要做好备份与升级评估。
整体社区讨论偏积极，认为文档和 Docker 体验在改善。
""".strip()

def build_model(model_env: str = "OPENAI_MODEL"):
    model = os.getenv(model_env) or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    kwargs: dict = {"temperature": 0}
    if base_url := os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = base_url
    if api_key := os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = api_key
    return init_chat_model(f"openai:{model}", **kwargs)

class _Title(BaseModel):
    title: str

class _Summary(BaseModel):
    summary: str

class _Keywords(BaseModel):
    keywords: list[str]

class _Sentiment(BaseModel):
    sentiment: Literal["positive", "neutral", "negative"]

def _branch(system: str, model, schema: type[BaseModel]):
    """单路：Prompt → 结构化输出（json_mode，兼容更多网关）。"""
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                system
                + " 只输出一个 json 对象，字段必须符合任务要求，不要 Markdown 代码块。",
            ),
            ("human", "{article}"),
        ]
    )
    structured = model.with_structured_output(schema, method="json_mode")
    return prompt | structured

def _field(obj: Any, name: str):
    if isinstance(obj, dict):
        return obj[name]
    return getattr(obj, name)

def build_pipeline(*, multi_model: bool = False):
    """
                 ┌─ 标题生成
    用户文章 ────┼─ 摘要生成
                 ├─ 情感分析
                 └─ 关键词提取
    """
    model_a = build_model("OPENAI_MODEL")
    # 多模型：B 使用 OPENAI_MODEL_B；未配置则与 A 相同（仍是四路并行）
    model_b = build_model("OPENAI_MODEL_B") if multi_model else model_a

    parallel = RunnableParallel(
        title=_branch("根据文章生成简洁中文标题。json 字段：title。", model_a, _Title),
        summary=_branch(
            "根据文章写两到三句中文摘要。json 字段：summary。", model_a, _Summary
        ),
        keywords=_branch(
            "提取 3～5 个中文关键词。json 字段：keywords（字符串数组）。",
            model_b,
            _Keywords,
        ),
        sentiment=_branch(
            "判断文章整体情感。json 字段：sentiment，取值 positive|neutral|negative。",
            model_b,
            _Sentiment,
        ),
    )

    def merge(parts: dict) -> dict:
        return ArticleAnalysis(
            title=_field(parts["title"], "title"),
            summary=_field(parts["summary"], "summary"),
            keywords=_field(parts["keywords"], "keywords"),
            sentiment=_field(parts["sentiment"], "sentiment"),
        ).model_dump()

    return (
        RunnableLambda(lambda text: {"article": text})
        | parallel
        | RunnableLambda(merge)
    )
