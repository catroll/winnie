"""Search-stage data model (``winnie.news.search/v1``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from winnie_news import SEARCH_SCHEMA

HitStatus = Literal["known", "new", "suspect"]


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    url: str
    snippet: str = ""
    source: str | None = None
    published_at: str | None = None
    query: str = ""
    status: HitStatus = "new"

    def as_source(self) -> dict[str, object]:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "snippet": self.snippet,
            "published_at": self.published_at,
        }


class EventSearch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str
    queries: list[str] = Field(default_factory=list)
    hits: list[SearchHit] = Field(default_factory=list)
    known: int = 0
    new: int = 0
    suspect: int = 0
    errors: list[str] = Field(default_factory=list)


class SearchBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: str = Field(default=SEARCH_SCHEMA, alias="schema")
    generated_at: str
    count: int = 0
    events: list[EventSearch] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def as_document(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)
