"""Deterministic review checks that run before the model reviews the draft.

These checks are the floor. A model review can be kind, distracted or
unavailable; a rule that finds a number in the draft that appears nowhere in the
material finds it every time. Each rule names what it checked and where, so the
writer gets a concrete instruction rather than "improve accuracy".
"""

from __future__ import annotations

import re

from winnie_news.aggregate.models import Event
from winnie_news.generate.models import Draft, WritingRequirements
from winnie_news.search.models import SearchHit

from .models import ReviewProblem

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_CLICKBAIT = (
    "震惊",
    "惊呆",
    "不看后悔",
    "万万没想到",
    "速看",
    "必看",
    "吓人",
    "炸裂",
    "shocking",
    "you won't believe",
    "unbelievable",
)
_BOILERPLATE = (
    "综上所述",
    "总的来说",
    "值得注意的是",
    "在这个快速发展的时代",
    "in conclusion",
    "it is worth noting",
    "in today's fast-paced world",
)


def rule_problems(
    draft: Draft,
    event: Event,
    hits: list[SearchHit],
    *,
    requirements: WritingRequirements,
) -> list[ReviewProblem]:
    problems: list[ReviewProblem] = []
    problems.extend(_format(draft, requirements))
    problems.extend(_facts(draft, event, hits))
    problems.extend(_completeness(draft, event))
    problems.extend(_style(draft, requirements))
    return problems


def _format(draft: Draft, requirements: WritingRequirements) -> list[ReviewProblem]:
    problems: list[ReviewProblem] = []
    title_limit = 40 if requirements.language == "zh" else 120
    if not draft.title.strip():
        problems.append(
            ReviewProblem(
                type="format", severity="high", origin="rule", detail="draft has no title"
            )
        )
    elif len(draft.title) > title_limit:
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail=f"title is {len(draft.title)} characters; limit is {title_limit}",
            )
        )
    body = draft.body.strip()
    if len(body) < 200:
        problems.append(
            ReviewProblem(
                type="format",
                severity="high",
                origin="rule",
                detail=f"body is only {len(body)} characters",
            )
        )
    if not draft.sources:
        problems.append(
            ReviewProblem(
                type="format",
                severity="high",
                origin="rule",
                detail="draft lists no sources",
            )
        )
    for source in draft.sources:
        if not source.url.startswith(("http://", "https://")):
            problems.append(
                ReviewProblem(
                    type="format",
                    severity="high",
                    origin="rule",
                    detail=f"source has no usable URL: {source.title or source.url!r}",
                )
            )
    return problems


def _facts(
    draft: Draft, event: Event, hits: list[SearchHit]
) -> list[ReviewProblem]:
    """Flag numbers in the body that appear nowhere in the material.

    This cannot prove a sentence is true. It can prove a number was not in the
    input, which is the one check that catches a model computing a total,
    converting a currency or simply inventing a figure.
    """
    corpus = _material_numbers(event, hits)
    unknown: list[str] = []
    for raw in _NUMBER_RE.findall(draft.body):
        number = raw.replace(",", "")
        if number.endswith(".0"):
            number = number[:-2]
        if number not in corpus and raw not in corpus and raw not in unknown:
            unknown.append(raw)
    if not unknown:
        return []
    preview = ", ".join(unknown[:8])
    return [
        ReviewProblem(
            type="fact",
            severity="high",
            origin="rule",
            detail=(
                f"numbers not present in the material: {preview}"
                + (f" (+{len(unknown) - 8} more)" if len(unknown) > 8 else "")
            ),
        )
    ]


def _material_numbers(event: Event, hits: list[SearchHit]) -> set[str]:
    texts: list[str] = []
    for article in event.articles:
        texts.extend(
            [
                article.title,
                article.excerpt,
                article.card.summary,
                " ".join(str(fact.value) for fact in article.card.key_numbers),
                " ".join(fact.name for fact in article.card.key_numbers),
            ]
        )
    for hit in hits:
        texts.extend([hit.title, hit.snippet])
    numbers: set[str] = set()
    for text in texts:
        for raw in _NUMBER_RE.findall(str(text)):
            numbers.add(raw)
            numbers.add(raw.replace(",", ""))
            if raw.replace(",", "").endswith(".0"):
                numbers.add(raw.replace(",", "")[:-2])
    return numbers


def _completeness(draft: Draft, event: Event) -> list[ReviewProblem]:
    problems: list[ReviewProblem] = []
    sources = {article.source for article in event.articles if article.source}
    if len(sources) < 2 and not draft.uncertainties:
        problems.append(
            ReviewProblem(
                type="completeness",
                severity="medium",
                origin="rule",
                detail=(
                    "event has a single source and the draft lists no uncertainty; "
                    "say what is unconfirmed or add a second report"
                ),
            )
        )
    if not draft.sources:
        return problems
    if len(draft.sources) == 1 and len(sources) > 1:
        problems.append(
            ReviewProblem(
                type="completeness",
                severity="low",
                origin="rule",
                detail="event has several sources but the draft cites only one",
            )
        )
    return problems


def _style(draft: Draft, requirements: WritingRequirements) -> list[ReviewProblem]:
    problems: list[ReviewProblem] = []
    text = f"{draft.title}\n{draft.body}".lower()
    for phrase in _CLICKBAIT:
        if phrase.lower() in text:
            problems.append(
                ReviewProblem(
                    type="style",
                    severity="high",
                    origin="rule",
                    detail=f"clickbait wording: {phrase!r}",
                )
            )
    if "!" in draft.title or "！" in draft.title:
        problems.append(
            ReviewProblem(
                type="style",
                severity="medium",
                origin="rule",
                detail="exclamation mark in the headline",
            )
        )
    for phrase in _BOILERPLATE:
        if phrase.lower() in text:
            problems.append(
                ReviewProblem(
                    type="style",
                    severity="low",
                    origin="rule",
                    detail=f"formulaic phrase: {phrase!r}",
                )
            )
    if not requirements.allow_commentary and _has_first_person_opinion(draft.body):
        problems.append(
            ReviewProblem(
                type="style",
                severity="medium",
                origin="rule",
                detail="the body contains first-person opinion but commentary is off",
            )
        )
    return problems


def _has_first_person_opinion(body: str) -> bool:
    markers = ("我认为", "我觉得", "笔者", "in my opinion", "i think", "we believe")
    lowered = body.lower()
    return any(marker in lowered for marker in markers)


def feedback_from(problems: list[ReviewProblem]) -> str:
    lines = [
        f"- [{problem.severity}] {problem.type}: {problem.detail}"
        for problem in problems
    ]
    return "\n".join(lines)
