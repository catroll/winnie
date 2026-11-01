"""Draft data model (``winnie.news.draft/v1``).

A draft is three things at once, and the fields say which is which:

* the **publishable text** — ``title``, ``content`` and ``tags``. This is what a
  person copies out, so it carries no source apparatus of any kind.
* the **digest line** — ``brief`` and ``category``, which let one batch be
  assembled into a dated, sectioned briefing without another model call.
* the **audit trail** — ``sources`` and ``uncertainties``. They are metadata and
  never appear in the text above; the review stage still refuses a draft whose
  sources are missing, because traceability is what makes the text checkable.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from winnie_news import DRAFT_SCHEMA

# The one-line form of a draft: at most this many characters. Kept as a module
# constant because the generator and the review rules must agree on it.
BRIEF_LIMIT = 40
# Tags a draft may carry at most. Two or three are normal for a narrow story; a
# writer that returns more than this is padding, so the generator trims.
TAG_LIMIT = 7
# The fewest tags a draft should carry, checked in review only: the shape is
# enforced by the generator, the taste by the editor.
TAG_MIN = 2


class DraftSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    url: str = ""
    source: str | None = None
    published_at: str | None = None


class Draft(BaseModel):
    """One article plus the metadata needed to check and publish it.

    ``sources`` is limited to URLs that existed in the input material. A model
    that invents a plausible-looking link would otherwise produce a draft that
    looks sourced and is not.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_name: str = Field(default=DRAFT_SCHEMA, alias="schema")
    event_id: str
    title: str
    # One of ``winnie_news.categories.CATEGORIES``. Empty only in a draft written
    # before the category existed; the digest derives one from the event then.
    category: str = ""
    brief: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    sources: list[DraftSource] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    language: str = "zh"
    model: str = ""
    generated_at: str
    revision: int = 1

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_body(cls, data: object) -> object:
        """Read a pre-tags draft that stored its text as ``body``.

        Runs written before this contract are still on disk under ``data/news``;
        without the alias a historical draft would validate with an empty
        content and render as a blank page.
        """
        if isinstance(data, dict) and not data.get("content") and data.get("body"):
            return {**data, "content": data["body"]}
        return data

    def as_document(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude_none=False)


class WritingRequirements(BaseModel):
    """What the writer is asked for, and what review measures it against.

    The two plugin contracts this merges set the defaults: a Chinese article of
    about 200 characters, extendable to 300 to explain background, and an
    explicit position only when the owner gave one.
    """

    model_config = ConfigDict(extra="forbid")

    language: str = "zh"
    # The requested length in characters, the floor below which a draft is
    # called thin, and the ceiling a draft is never allowed to exceed.
    target_chars: int = 200
    min_chars: int = 150
    max_chars: int = 300
    audience: str = "general readers"
    style: str = "plain news report"
    # Empty means neutral reporting. Anything else is the position the text must
    # present plainly instead of hiding behind false balance.
    stance: str = ""
    allow_commentary: bool = False

    @model_validator(mode="after")
    def _ordered_limits(self) -> "WritingRequirements":
        if not (0 < self.min_chars <= self.target_chars <= self.max_chars):
            raise ValueError(
                "writing limits must satisfy 0 < min_chars <= target_chars <= max_chars"
            )
        return self
