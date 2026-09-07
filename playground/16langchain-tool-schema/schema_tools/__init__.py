"""Pydantic 定义 Tool 入参 → 自动 JSON Schema → 调用前校验。"""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool
from pydantic import BaseModel, Field, field_validator


class SearchInput(BaseModel):
    keyword: str = Field(description="搜索关键词，非空")
    limit: int = Field(default=3, ge=1, le=10, description="返回条数 1～10")
    sort: Literal["relevance", "time"] = Field(default="relevance")

    @field_validator("keyword")
    @classmethod
    def keyword_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("keyword must not be blank")
        return v


@tool(args_schema=SearchInput)
def search(keyword: str, limit: int = 3, sort: str = "relevance") -> str:
    """按关键词搜索演示语料库。"""
    rows = [f"{i}. ({sort}) result for {keyword}" for i in range(1, limit + 1)]
    return "\n".join(rows)


class BookInput(BaseModel):
    title: str = Field(min_length=1, description="书名")
    year: int = Field(ge=1900, le=2100, description="出版年")


@tool(args_schema=BookInput)
def catalog_add(title: str, year: int) -> str:
    """登记一本书到演示目录。"""
    return f"cataloged: 《{title}》 ({year})"
