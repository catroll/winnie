"""The digest document (``winnie.news.digest/v1``).

A digest is the batch view of one run: the drafts that passed review, dated,
sorted newest first, grouped into the four sections and cut to a number of
items. It holds no prose of its own — every title and brief comes from a draft
that already passed a review — so the digest cannot say anything the drafts did
not, and re-rendering it in another format costs nothing and calls no model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from winnie_news import DIGEST_SCHEMA
from winnie_news.categories import CATEGORIES

DigestFormat = Literal["standard", "brief", "table"]
FORMATS: tuple[str, ...] = ("standard", "brief", "table")


class DigestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Calendar days ending today: 2 means today and yesterday. 0 disables the
    # window, which is only useful for a backfill or a test.
    window_days: int = Field(default=2, ge=0)
    # The most items one digest carries; more recent wins.
    max_items: int = Field(default=30, ge=0)
    # The format the Markdown file on disk uses. A request may ask for another.
    format: DigestFormat = "standard"
    # A draft that did not pass review is left out by default: the digest is a
    # publishable list, and review is what makes a draft publishable.
    include_needs_review: bool = False


class DigestItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str
    category: str
    # YYYY-MM-DD: the day the event happened, not the day it was written.
    date: str
    title: str
    brief: str = ""
    tags: list[str] = Field(default_factory=list)
    event_type: str = ""


class DigestSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str
    items: list[DigestItem] = Field(default_factory=list)


class Digest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: str = Field(default=DIGEST_SCHEMA, alias="schema")
    generated_at: str
    window_days: int
    start_date: str
    end_date: str
    # What the digest asked for, so "3 of 30" is visible without the config.
    requested: int
    count: int
    format: DigestFormat = "standard"
    sections: list[DigestSection] = Field(default_factory=list)
    # event_id -> why it is not in the digest. Keeps "the digest is short" and
    # "review rejected these" from looking the same.
    excluded: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    def items(self) -> list[DigestItem]:
        return [item for section in self.sections for item in section.items]

    def as_document(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


def empty_sections() -> list[DigestSection]:
    return [DigestSection(category=category) for category in CATEGORIES]
