"""新闻分析的结构化 Schema：Pydantic 即程序可消费的合约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

class NewsAnalysis(BaseModel):
    """LLM 应填满的结构化结果（再交给下游程序）。"""

    title: str = Field(description="新闻标题；原文无标题则概括一条")
    summary: str = Field(description="两到三句中文摘要")
    keywords: list[str] = Field(description="3～5 个关键词")
    sentiment: Literal["positive", "neutral", "negative"] = Field(
        description="整体情感倾向"
    )

# 同一合约的 JSON Schema 视图（部分模型 / 工具链直接吃 schema）
NEWS_JSON_SCHEMA = NewsAnalysis.model_json_schema()

DEFAULT_NEWS = """
【示例】开源向量数据库 Qdrant 发布新版本，强调过滤查询与单机部署体验。
社区反馈积极，有团队计划将其用于个人知识库检索；也有人提醒需关注备份与版本升级成本。
""".strip()
