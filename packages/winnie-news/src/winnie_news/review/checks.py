"""Deterministic review checks that run before the model reviews the draft.

These checks are the floor. A model review can be kind, distracted or
unavailable; a rule that finds a number in the draft that appears nowhere in the
material finds it every time. Each rule names what it checked and where, so the
writer gets a concrete instruction rather than "improve accuracy".

The rules follow the two prompt contracts: the drafting contract (about 200
characters, a headline, paragraphs of plain prose, tags, no source apparatus)
and the digest contract (one of four sections, a brief that fits a morning
list). The model-owned judgement — is the fact true, is the position well
argued — is left to the reviewer prompt; anything mechanical is decided here.
"""

from __future__ import annotations

import re

from winnie_news.aggregate.models import Event
from winnie_news.categories import CATEGORIES
from winnie_news.generate.models import (
    BRIEF_LIMIT,
    TAG_LIMIT,
    TAG_MIN,
    Draft,
    WritingRequirements,
)
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
# A link in the publishable text is forbidden outright: sources live in the
# draft JSON, not in the article.
_LINK_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\([^)\n]*\)")
# Markdown structure at the start of a line: heading, list item, quote, fence.
_MD_BLOCK_RE = re.compile(
    r"^[ \t]{0,3}(?:#{1,6}[ \t]|[-*+][ \t]|\d+[.)][ \t]|>[ \t]|```)",
    re.MULTILINE,
)
# A named outlet citation. "据报道" and "尚待核实" stay allowed: the digest
# contract asks for exactly those markers on unverified facts.
_CITATION_RE = re.compile(
    r"据[\u4e00-\u9fffA-Za-z]{2,12}(?:报道|消息)"
    r"|^[ \t]*(?:来源|出处|参考资料)[ \t]*[:：]",
    re.MULTILINE,
)
# Chinese state leaders are never tags, and "中国" must not stand in for one.
_LEADER_NAMES = (
    "习近平",
    "李强",
    "赵乐际",
    "王沪宁",
    "蔡奇",
    "丁薛祥",
    "李希",
    "王毅",
    "毛泽东",
    "邓小平",
    "江泽民",
    "胡锦涛",
    "朱镕基",
    "温家宝",
    "李克强",
)
_GENERIC_TAGS = {"中国", "法律", "经济", "时事"}
_FALSE_BALANCE = (
    "有人认为",
    "也有人认为",
    "有分析认为",
    "被一些人视为",
    "部分网友",
    "引发讨论",
    "引发热议",
    "sparked discussion",
    "some say",
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
    problems.extend(_tags(draft))
    problems.extend(_text_hygiene(draft))
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

    content = draft.content.strip()
    if len(content) < 120:
        problems.append(
            ReviewProblem(
                type="format",
                severity="high",
                origin="rule",
                detail=(
                    f"content is only {len(content)} characters; nothing useful "
                    "can stand on that"
                ),
            )
        )
    elif len(content) > requirements.max_chars:
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail=(
                    f"content is {len(content)} characters; the budget is "
                    f"{requirements.max_chars} (about {requirements.target_chars})"
                ),
            )
        )
    elif len(content) < requirements.min_chars:
        problems.append(
            ReviewProblem(
                type="format",
                severity="low",
                origin="rule",
                detail=(
                    f"content is {len(content)} characters, under the "
                    f"~{requirements.target_chars} target; fine only when the "
                    "material is thin"
                ),
            )
        )

    paragraphs = [part for part in re.split(r"\n[ \t]*\n", content) if part.strip()]
    if len(paragraphs) < 2 and len(content) >= requirements.min_chars:
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail="the article is one block; split it where the subject changes",
            )
        )

    if draft.category not in CATEGORIES:
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail=(
                    f"category {draft.category!r} is not one of "
                    + ", ".join(CATEGORIES)
                ),
            )
        )
    brief = draft.brief.strip()
    if not brief:
        problems.append(
            ReviewProblem(
                type="format",
                severity="low",
                origin="rule",
                detail="the draft has no brief for the digest",
            )
        )
    elif len(brief) > BRIEF_LIMIT:
        problems.append(
            ReviewProblem(
                type="format",
                severity="low",
                origin="rule",
                detail=(
                    f"brief is {len(brief)} characters; the one-line form aims "
                    f"at {BRIEF_LIMIT}"
                ),
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


def _tags(draft: Draft) -> list[ReviewProblem]:
    problems: list[ReviewProblem] = []
    tags = [tag for tag in draft.tags if tag.strip()]
    if not tags:
        problems.append(
            ReviewProblem(
                type="format",
                severity="high",
                origin="rule",
                detail="the draft carries no tags",
            )
        )
        return problems
    if len(tags) < TAG_MIN:
        problems.append(
            ReviewProblem(
                type="format",
                severity="low",
                origin="rule",
                detail=f"only {len(tags)} tag; two or three at least",
            )
        )
    elif len(tags) > TAG_LIMIT:
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail=f"{len(tags)} tags; the limit is {TAG_LIMIT}",
            )
        )
    for tag in tags:
        if "人工智能" in tag:
            problems.append(
                ReviewProblem(
                    type="style",
                    severity="medium",
                    origin="rule",
                    detail=f"tag {tag!r}: write AI, never 人工智能",
                )
            )
        if any(name in tag for name in _LEADER_NAMES):
            problems.append(
                ReviewProblem(
                    type="compliance",
                    severity="high",
                    origin="rule",
                    detail=(
                        f"tag {tag!r} carries a Chinese state leader's name; "
                        "leaders are never tags"
                    ),
                )
            )
        if tag in _GENERIC_TAGS:
            problems.append(
                ReviewProblem(
                    type="style",
                    severity="low",
                    origin="rule",
                    detail=(
                        f"tag {tag!r} is too generic; name the place or the "
                        "subject instead"
                    ),
                )
            )
    for index, tag in enumerate(tags):
        for other in tags[index + 1 :]:
            if tag != other and (tag in other or other in tag):
                problems.append(
                    ReviewProblem(
                        type="style",
                        severity="low",
                        origin="rule",
                        detail=f"tags {tag!r} and {other!r} repeat the same subject",
                    )
                )
    return problems


def _text_hygiene(draft: Draft) -> list[ReviewProblem]:
    """The publishable text carries no link and no named source.

    Numbers and names are checked against the material elsewhere; this rule is
    about the *shape* of the output the drafting contract promises.
    """
    problems: list[ReviewProblem] = []
    text = "\n".join([draft.title, draft.content, draft.brief, *draft.tags])
    if _LINK_RE.search(text) or _MD_LINK_RE.search(text):
        problems.append(
            ReviewProblem(
                type="format",
                severity="high",
                origin="rule",
                detail=(
                    "the publishable text contains a link; sources belong in "
                    "the draft metadata"
                ),
            )
        )
    for match in _CITATION_RE.finditer(text):
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail=f"source attribution in the publishable text: {match.group(0).strip()!r}",
            )
        )
    if _MD_BLOCK_RE.search(draft.content):
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail="the content contains Markdown structure; plain prose only",
            )
        )
    return problems


def _facts(
    draft: Draft, event: Event, hits: list[SearchHit]
) -> list[ReviewProblem]:
    """Flag numbers in the prose that appear nowhere in the material.

    The headline and the brief are published too — the brief reaches the digest
    — so they are scanned with the article. Tags are excluded on purpose: a tag
    is a label, not a claim, and a tag like "2级地震" would otherwise fail a
    fact check for a shape that the tag rules already reject. This cannot prove
    a sentence is true; it can prove a number was not in the input, which is the
    one check that catches a model computing a total, converting a currency or
    inventing a figure.
    """
    corpus = _material_numbers(event, hits)
    published = "\n".join([draft.title, draft.content, draft.brief])
    unknown: list[str] = []
    for raw in _NUMBER_RE.findall(published):
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
    text = f"{draft.title}\n{draft.content}".lower()
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
    if not requirements.allow_commentary and _has_first_person_opinion(draft.content):
        problems.append(
            ReviewProblem(
                type="style",
                severity="medium",
                origin="rule",
                detail="the content contains first-person opinion but commentary is off",
            )
        )
    if requirements.stance.strip():
        for phrase in _FALSE_BALANCE:
            if phrase.lower() in text:
                problems.append(
                    ReviewProblem(
                        type="style",
                        severity="low",
                        origin="rule",
                        detail=(
                            f"a position is set but the text hedges with {phrase!r}; "
                            "present it plainly"
                        ),
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
