"""Review data model (``winnie.news.review/v1``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from winnie_news import REVIEW_SCHEMA

ProblemType = Literal["format", "fact", "completeness", "style", "compliance"]
Severity = Literal["high", "medium", "low"]
ReviewStatus = Literal["passed", "retry", "needs_human"]


class ReviewProblem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: ProblemType = "fact"
    severity: Severity = "medium"
    detail: str
    # "rule" or "llm": a reader must know whether a person or a heuristic said it.
    origin: str = "llm"


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: str = Field(default=REVIEW_SCHEMA, alias="schema")
    event_id: str
    passed: bool = False
    score: float = 0.0
    problems: list[ReviewProblem] = Field(default_factory=list)
    feedback: str = ""
    retry: int = 0
    max_retries: int = 2
    status: ReviewStatus = "retry"
    checked_at: str

    def as_document(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)

    def high_problems(self) -> list[ReviewProblem]:
        return [problem for problem in self.problems if problem.severity == "high"]
