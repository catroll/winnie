"""Aggregation data model: event cards, events and the event batch.

The event type taxonomy is fixed and two-level. A model that invents a type
makes events incomparable across batches, and comparability is the whole point
of this stage, so the type is validated against the enum and anything unknown
becomes ``other``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from winnie_news import EVENT_SCHEMA

EVENT_TYPES: tuple[str, ...] = (
    "disaster.earthquake",
    "disaster.flood",
    "disaster.typhoon",
    "disaster.other",
    "accident.transport",
    "accident.industrial",
    "accident.fire",
    "accident.other",
    "policy.regulation",
    "policy.legislation",
    "policy.appointment",
    "policy.other",
    "corporate.earnings",
    "corporate.ma",
    "corporate.product",
    "corporate.other",
    "politics.election",
    "politics.diplomacy",
    "politics.protest",
    "politics.other",
    "security.conflict",
    "security.crime",
    "security.terror",
    "security.other",
    "health.outbreak",
    "health.treatment",
    "health.other",
    "tech.product",
    "tech.research",
    "tech.other",
    "sports.match",
    "sports.transfer",
    "sports.other",
    "judicial.case",
    "judicial.verdict",
    "judicial.other",
    "economy.macro",
    "economy.trade",
    "economy.other",
    "society.education",
    "society.weather",
    "society.other",
    "other",
)

_TYPE_ALIASES: dict[str, str] = {
    # Chinese labels the sources and models use.
    "地震": "disaster.earthquake",
    "洪灾": "disaster.flood",
    "洪水": "disaster.flood",
    "台风": "disaster.typhoon",
    "交通事故": "accident.transport",
    "车祸": "accident.transport",
    "工厂事故": "accident.industrial",
    "火灾": "accident.fire",
    "监管": "policy.regulation",
    "法规": "policy.regulation",
    "立法": "policy.legislation",
    "法案": "policy.legislation",
    "人事任命": "policy.appointment",
    "财报": "corporate.earnings",
    "业绩": "corporate.earnings",
    "并购": "corporate.ma",
    "收购": "corporate.ma",
    "产品发布": "corporate.product",
    "选举": "politics.election",
    "外交": "politics.diplomacy",
    "抗议": "politics.protest",
    "冲突": "security.conflict",
    "犯罪": "security.crime",
    "恐袭": "security.terror",
    "疫情": "health.outbreak",
    "医疗": "health.treatment",
    "科技发布": "tech.product",
    "科研": "tech.research",
    "比赛": "sports.match",
    "转会": "sports.transfer",
    "案件": "judicial.case",
    "判决": "judicial.verdict",
    "宏观经济": "economy.macro",
    "贸易": "economy.trade",
    "教育": "society.education",
    "天气": "society.weather",
    # English labels.
    "earthquake": "disaster.earthquake",
    "flood": "disaster.flood",
    "typhoon": "disaster.typhoon",
    "traffic accident": "accident.transport",
    "fire": "accident.fire",
    "industrial accident": "accident.industrial",
    "regulation": "policy.regulation",
    "legislation": "policy.legislation",
    "appointment": "policy.appointment",
    "earnings": "corporate.earnings",
    "merger": "corporate.ma",
    "acquisition": "corporate.ma",
    "product launch": "corporate.product",
    "election": "politics.election",
    "diplomacy": "politics.diplomacy",
    "protest": "politics.protest",
    "conflict": "security.conflict",
    "crime": "security.crime",
    "terror": "security.terror",
    "outbreak": "health.outbreak",
    "treatment": "health.treatment",
    "research": "tech.research",
    "match": "sports.match",
    "transfer": "sports.transfer",
    "case": "judicial.case",
    "verdict": "judicial.verdict",
    "macro": "economy.macro",
    "trade": "economy.trade",
    "education": "society.education",
    "weather": "society.weather",
}

# Keyword hints used when the type field is absent or unusable.
TYPE_HINTS: dict[str, str] = {
    "地震": "disaster.earthquake",
    "洪水": "disaster.flood",
    "台风": "disaster.typhoon",
    "车祸": "accident.transport",
    "火灾": "accident.fire",
    "法案": "policy.legislation",
    "政策": "policy.regulation",
    "财报": "corporate.earnings",
    "营收": "corporate.earnings",
    "收购": "corporate.ma",
    "选举": "politics.election",
    "疫情": "health.outbreak",
    "比赛": "sports.match",
    "判决": "judicial.verdict",
    "发布": "tech.product",
    "earthquake": "disaster.earthquake",
    "flood": "disaster.flood",
    "typhoon": "disaster.typhoon",
    "fire": "accident.fire",
    "election": "politics.election",
    "earnings": "corporate.earnings",
    "acquisition": "corporate.ma",
    "verdict": "judicial.verdict",
    "outbreak": "health.outbreak",
}

TYPE_WINDOW_DAYS: dict[str, int] = {
    "disaster": 7,
    "accident": 7,
    "politics": 14,
    "policy": 14,
    "corporate": 3,
    "sports": 2,
    "judicial": 30,
    "health": 14,
    "economy": 7,
}


def normalize_event_type(raw: object) -> str:
    """Map a free label to the fixed taxonomy, never inventing a new code."""
    text = str(raw or "").strip()
    lowered = text.lower()
    if lowered in EVENT_TYPES:
        return lowered
    if text in _TYPE_ALIASES:
        return _TYPE_ALIASES[text]
    if lowered in _TYPE_ALIASES:
        return _TYPE_ALIASES[lowered]
    for key, value in _TYPE_ALIASES.items():
        if key in text or key in lowered:
            return value
    for key, value in TYPE_HINTS.items():
        if key in text or key in lowered:
            return value
    return "other"


def type_family(event_type: str) -> str:
    return str(event_type).split(".", 1)[0]


class TimeInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start: str | None = None
    end: str | None = None
    granularity: Literal["hour", "day", "period", "unknown"] = "unknown"


class NamedEntity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    aliases: list[str] = Field(default_factory=list)
    # Locations only: city/district/province/region/country, used to allow a
    # country to match one of its cities without allowing two countries to.
    level: str | None = None


class NumberFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    value: float | str
    unit: str | None = None


class EventCard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_type: str = "other"
    event_time: TimeInfo = Field(default_factory=TimeInfo)
    actors: list[NamedEntity] = Field(default_factory=list)
    location: list[NamedEntity] = Field(default_factory=list)
    action: str = ""
    object: str = ""
    key_numbers: list[NumberFact] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    summary: str = ""
    confidence: float = 0.5


class VerifyResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: Literal["same", "different", "uncertain"] = "uncertain"
    confidence: float = 0.5
    reason: str = ""
    conflicting_slots: list[str] = Field(default_factory=list)


class ArticleRef(BaseModel):
    """One article as it belongs to an event.

    The card is kept per article: a later article may correct a number, and the
    draft must be able to attribute the correction to a source.
    """

    model_config = ConfigDict(extra="ignore")

    material_id: str
    title: str = ""
    source: str = ""
    url: str | None = None
    published_at: str | None = None
    language: str | None = None
    # The cleaned body truncated for the writing prompt. The event file stays
    # small; the draft only needs the top coverage, not every byte.
    excerpt: str = ""
    score: float | None = None
    method: str | None = None
    card: EventCard = Field(default_factory=EventCard)


class EventCoverage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    articles: int = 0
    sources: int = 0
    languages: int = 0
    first_report: str | None = None
    latest_report: str | None = None


class Event(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str = ""
    summary: str = ""
    event_type: str = "other"
    status: str = "developing"
    first_seen: str
    last_seen: str
    created_at: str
    card: EventCard = Field(default_factory=EventCard)
    keywords: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    locations: list[NamedEntity] = Field(default_factory=list)
    coverage: EventCoverage = Field(default_factory=EventCoverage)
    articles: list[ArticleRef] = Field(default_factory=list)

    def refresh(self) -> None:
        """Recompute derived fields from the articles the event holds."""
        self.summary = self.summary or self.card.summary or self.title
        self.event_type = self.card.event_type
        sources = {article.source for article in self.articles if article.source}
        languages = {
            str(article.language).lower()
            for article in self.articles
            if article.language
        }
        published = [a.published_at for a in self.articles if a.published_at]
        self.coverage = EventCoverage(
            articles=len(self.articles),
            sources=len(sources),
            languages=len(languages) if languages else (1 if self.articles else 0),
            first_report=min(published) if published else None,
            latest_report=max(published) if published else None,
        )


class AggregateDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    material_id: str
    event_id: str | None = None
    decision: Literal["new", "merge", "review", "skipped"]
    method: str = ""
    score: float | None = None
    reason: str = ""


class EventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: str = Field(default=EVENT_SCHEMA, alias="schema")
    generated_at: str
    count: int = 0
    events: list[Event] = Field(default_factory=list)
    decisions: list[AggregateDecision] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def as_document(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)
