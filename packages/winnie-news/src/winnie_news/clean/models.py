"""Clean-stage data model (``winnie.news.clean/v1``)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from winnie_news import CLEAN_SCHEMA


class CleanQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep: bool = True
    score: float = 0.5
    reason: str = ""


class CleanItem(BaseModel):
    """One material item with its extracted facts.

    The fields mirror the material contract plus what the extractor found. A
    field the extractor was unsure about stays empty: an invented location would
    propagate into event cards and then into the draft.
    """

    model_config = ConfigDict(extra="forbid")

    material_id: str
    title: str
    content: str = ""
    summary: str = ""
    source: str = ""
    url: str | None = None
    published_at: str | None = None
    language: str | None = None
    event_type: str | None = None
    entities: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    event_time: str | None = None
    key_numbers: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    topic_keywords: list[str] = Field(default_factory=list)
    quality: CleanQuality = Field(default_factory=CleanQuality)
    extra: dict[str, object] = Field(default_factory=dict)


class DroppedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str
    title: str | None = None
    stage: str
    reason: str


class CleanBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: str = Field(default=CLEAN_SCHEMA, alias="schema")
    generated_at: str
    count: int = 0
    items: list[CleanItem] = Field(default_factory=list)
    dropped: list[DroppedItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def model_post_init(self, _context: object) -> None:
        self.count = len(self.items)

    def as_document(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)
