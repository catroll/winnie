"""The review stage: rules first, then one model review, then a verdict.

The verdict has three states. ``passed`` means publishable. ``retry`` means the
feedback names a fix the writer can attempt. ``needs_human`` means the retry
budget is spent (or the reviewer could not run): the draft is kept with every
problem, and nothing is silently published.

The retry counter belongs to the pipeline; this stage only reports which verdict
follows from ``retry`` and ``max_retries``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from winnie_news.aggregate.models import Event
from winnie_news.common.jsonparse import JSONReplyError, parse_json_object
from winnie_news.common.timeutil import now_iso
from winnie_news.generate.models import Draft, WritingRequirements
from winnie_news.ports import LLMPort
from winnie_news.search.models import SearchHit

from .checks import feedback_from, rule_problems
from .models import ReviewProblem, ReviewResult

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"
PURPOSE = "news.review"
MAX_RETRIES = 2

SYSTEM_PROMPT = """\
You are the editor of a news service. You receive one draft and the material it
was written from. You check the draft for errors and decide whether it may run.

Output one JSON object and nothing else:
{"passed": true | false,
 "score": 0.0-1.0,
 "problems": [{"type": "format"|"fact"|"completeness"|"style"|"compliance",
               "severity": "high"|"medium"|"low",
               "detail": "what is wrong and where"}],
 "feedback": "instructions for the writer, concrete and minimal"}

Check:
- fact: does every number, name, place and time match the material? Is a claim
  attributed to a source that does not say it? Are two different events mixed?
- completeness: is one side of the story missing? Is an old event presented as
  new? Is an important update from a later article omitted?
- style: clickbait, unsupported commentary, formulaic AI prose.
- compliance: private information, minors, gratuitous detail of violence.
- format: missing fields, broken links, an empty body.

An empty "problems" list with passed=true is a real decision, not a default.
Do not invent problems to look thorough, and do not pass a draft whose central
fact you cannot find in the material.
"""


class ReviewerReply(BaseModel):
    model_config = ConfigDict(extra="ignore")

    passed: bool = False
    score: float = 0.0
    problems: list[ReviewProblem] = Field(default_factory=list)
    feedback: str = ""


def build_messages(
    draft: Draft,
    event: Event,
    hits: list[SearchHit],
    *,
    requirements: WritingRequirements,
) -> list[dict[str, Any]]:
    material = {
        "event": {
            "id": event.id,
            "title": event.title,
            "summary": event.summary,
            "type": event.card.event_type,
            "time": event.card.event_time.model_dump(),
            "actors": [entity.name for entity in event.card.actors],
            "locations": [entity.name for entity in event.card.location],
            "key_numbers": [fact.model_dump() for fact in event.card.key_numbers],
            "coverage": event.coverage.model_dump(),
        },
        "articles": [
            {
                "title": article.title,
                "source": article.source,
                "url": article.url,
                "published_at": article.published_at,
                "summary": article.card.summary,
                "excerpt": article.excerpt,
                "key_numbers": [
                    fact.model_dump() for fact in article.card.key_numbers
                ],
            }
            for article in event.articles
        ],
        "search_leads": [
            {"title": hit.title, "url": hit.url, "snippet": hit.snippet, "status": hit.status}
            for hit in hits
        ],
        "requirements": requirements.model_dump(),
    }
    content = (
        "<material>\n"
        + json.dumps(material, ensure_ascii=False)
        + "\n</material>\n<draft>\n"
        + json.dumps(draft.as_document(), ensure_ascii=False)
        + "\n</draft>"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


async def review_draft(
    draft: Draft,
    event: Event,
    hits: list[SearchHit],
    llm: LLMPort,
    *,
    requirements: WritingRequirements | None = None,
    retry: int = 0,
    max_retries: int = MAX_RETRIES,
) -> ReviewResult:
    """Run the rule checks and one model review; return the verdict."""
    req = requirements or WritingRequirements()
    problems = rule_problems(draft, event, hits, requirements=req)

    reply: ReviewerReply | None = None
    review_error = ""
    try:
        completion = await llm.complete(
            build_messages(draft, event, hits, requirements=req),
            purpose=PURPOSE,
        )
        reply = ReviewerReply.model_validate(parse_json_object(completion.text))
    except JSONReplyError as exc:
        review_error = f"the reviewer returned no JSON: {exc}"
    except Exception as exc:  # noqa: BLE001 - a failed review is a retry, not a pass
        review_error = f"the reviewer failed: {type(exc).__name__}: {exc}"

    if reply is not None:
        for problem in reply.problems:
            problem.origin = "llm"
            problems.append(problem)
    problems = _dedupe(problems)

    high = [problem for problem in problems if problem.severity == "high"]
    medium = [problem for problem in problems if problem.severity == "medium"]
    llm_passed = bool(reply.passed) if reply is not None else False
    score = reply.score if reply is not None else 0.0
    score = max(0.0, min(1.0, score - 0.2 * len(high) - 0.05 * len(medium)))
    passed = llm_passed and not high and not review_error

    if review_error:
        problems.append(
            ReviewProblem(
                type="format",
                severity="high",
                origin="rule",
                detail=review_error,
            )
        )

    if passed:
        status = "passed"
    elif retry + 1 > max_retries:
        status = "needs_human"
    else:
        status = "retry"

    feedback_parts = []
    if reply is not None and reply.feedback.strip():
        feedback_parts.append(reply.feedback.strip())
    rules_only = [problem for problem in problems if problem.origin == "rule"]
    if rules_only:
        feedback_parts.append("Fix these rule violations:\n" + feedback_from(rules_only))
    feedback = "\n\n".join(feedback_parts).strip()

    result = ReviewResult(
        event_id=draft.event_id,
        passed=passed,
        score=round(score, 4),
        problems=problems,
        feedback=feedback,
        retry=retry,
        max_retries=max_retries,
        status=status,
        checked_at=now_iso(),
    )
    logger.info(
        "review %s: %s (score %.2f, %d problems)",
        draft.event_id,
        status,
        result.score,
        len(problems),
    )
    return result


def _dedupe(problems: list[ReviewProblem]) -> list[ReviewProblem]:
    seen: set[tuple[str, str]] = set()
    result: list[ReviewProblem] = []
    for problem in problems:
        key = (problem.type, problem.detail.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(problem)
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(result, key=lambda item: order.get(item.severity, 3))
