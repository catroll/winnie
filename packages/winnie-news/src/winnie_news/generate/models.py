"""Draft data model (``winnie.news.draft/v1``)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from winnie_news import DRAFT_SCHEMA


class DraftSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    url: str = ""
    source: str | None = None
    published_at: str | None = None


class Draft(BaseModel):
    """A news draft plus everything needed to check it.

    ``sources`` is limited to URLs that existed in the input material. A model
    that invents a plausible-looking link would otherwise produce a draft that
    looks sourced and is not.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_name: str = Field(default=DRAFT_SCHEMA, alias="schema")
    event_id: str
    title: str
    subtitle: str | None = None
    body: str = ""
    sources: list[DraftSource] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    language: str = "zh"
    model: str = ""
    generated_at: str
    revision: int = 1

    def as_document(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude_none=False)


class WritingRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = "zh"
    # Rough target length of the body, in characters. The prompt asks for a
    # range around it; the review stage does not reject on length alone.
    target_chars: int = 800
    audience: str = "general readers"
    style: str = "neutral wire-service report"
    allow_commentary: bool = False
