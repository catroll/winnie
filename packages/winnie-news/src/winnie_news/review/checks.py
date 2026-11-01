"""Deterministic review checks that run before the model reviews the draft.

Every rule here is traceable to one of two sources, and says which:

* a quoted editor-plugin rule — the detail names the rule and repeats its own
  numbers ("内容规则 1", "标签规则 9"), so the writer gets the owner's wording
  back rather than a reviewer's reinterpretation;
* a pipeline rule that pre-dates the plugins and is labelled as such: headline
  length, the sources trace, the number-versus-material check, clickbait and
  boilerplate lists.

Nothing else is invented. Where a plugin says "约200字" or "尽量不超过40字", the
check uses that wording (a soft note), not a stricter threshold chosen here. The
model review then applies the same quoted skills.
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
# Pipeline style rules that pre-date the editor plugins.
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
# 来源与引用规则 1: the output never carries a link.
_LINK_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\([^)\n]*\)")
# 来源与引用规则 5: a named outlet citation is source information, not a fact.
# "据报道" and "尚待核实" stay allowed: they are the required markers on an
# unverified claim (内容规则 4).
_CITATION_RE = re.compile(
    r"据[\u4e00-\u9fffA-Za-z]{2,12}(?:报道|消息)"
    r"|^[ \t]*(?:来源|出处|参考资料)[ \t]*[:：]",
    re.MULTILINE,
)
# 输出规则: plain text, no Markdown structure.
_MD_BLOCK_RE = re.compile(
    r"^[ \t]{0,3}(?:#{1,6}[ \t]|[-*+][ \t]|\d+[.)][ \t]|>[ \t]|```)",
    re.MULTILINE,
)
# 标签规则 9 names these leaders and extends to "历任党政领导人".
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
# 标签规则 11: never the whole story as one of these.
_GENERIC_TAGS = {"中国", "法律", "经济", "时事"}
# 立场与表达规则 2 and 3 name these hedges verbatim.
_FALSE_BALANCE = (
    "有人认为",
    "也有人认为",
    "获得部分网友评价",
    "被一些人视为",
    "引发讨论",
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
    # Pipeline rule: a headline that does not fit one line is unreadable in the
    # console and in the digest. The plugin only says "简洁".
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
                detail=f"title is {len(draft.title)} characters; the pipeline cap is {title_limit}",
            )
        )

    content = draft.content.strip()
    if not content:
        problems.append(
            ReviewProblem(
                type="format", severity="high", origin="rule", detail="draft has no content"
            )
        )
    elif len(content) > requirements.max_chars:
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail=(
                    f"content is {len(content)} characters; news-drafting 内容规则 1 "
                    f"allows about {requirements.target_chars}, extendable to "
                    f"{requirements.max_chars}"
                ),
            )
        )
    elif len(content) < requirements.target_chars:
        problems.append(
            ReviewProblem(
                type="format",
                severity="low",
                origin="rule",
                detail=(
                    f"content is {len(content)} characters, under the "
                    f"~{requirements.target_chars} of 内容规则 1; 内容规则 8 allows "
                    "a shorter draft when the material cannot support more"
                ),
            )
        )

    # 内容规则 9 and 10: semantic paragraphs, one blank line between them. Checked
    # at the plugin's own target length, where a single block is no longer a
    # deliberate short piece.
    if len(content) >= requirements.target_chars and not re.search(
        r"\n[ \t]*\n", content
    ):
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail=(
                    "content is one block; 内容规则 9/10 require paragraphs "
                    "separated by one blank line"
                ),
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
                    + " (hot-news 整理规则 分类)"
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
                detail="the draft has no brief; hot-news 晨报/晚报 expects a 一句话速览",
            )
        )
    elif len(brief) > BRIEF_LIMIT:
        problems.append(
            ReviewProblem(
                type="format",
                severity="low",
                origin="rule",
                detail=(
                    f"brief is {len(brief)} characters; hot-news says "
                    f"尽量不超过{BRIEF_LIMIT}字"
                ),
            )
        )

    # Pipeline rule: the sources trace. It is not part of the 成稿 (来源与引用规则
    # 1 forbids that), so it is checked in the metadata.
    if not draft.sources:
        problems.append(
            ReviewProblem(
                type="format",
                severity="high",
                origin="rule",
                detail="draft lists no sources; the pipeline cannot trace the text",
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
                detail="the draft carries no tags; 输出规则 requires a tags line",
            )
        )
        return problems
    if len(tags) > TAG_LIMIT:
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail=f"{len(tags)} tags; 标签规则 1 allows at most {TAG_LIMIT}",
            )
        )
    elif len(tags) < TAG_MIN:
        problems.append(
            ReviewProblem(
                type="format",
                severity="low",
                origin="rule",
                detail=(
                    f"only {len(tags)} tag; 标签规则 1 says about five, or two or "
                    "three when the story has only one or two sides"
                ),
            )
        )
    for tag in tags:
        if "人工智能" in tag:
            problems.append(
                ReviewProblem(
                    type="style",
                    severity="medium",
                    origin="rule",
                    detail=f"tag {tag!r}: 标签规则 5 says write AI, never 人工智能",
                )
            )
        if any(name in tag for name in _LEADER_NAMES):
            problems.append(
                ReviewProblem(
                    type="compliance",
                    severity="high",
                    origin="rule",
                    detail=(
                        f"tag {tag!r}: 标签规则 9 keeps a Chinese state leader's "
                        "name out of tags"
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
                        f"tag {tag!r}: 标签规则 11 allows it only when the article "
                        "is really about that subject"
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
                        detail=(
                            f"tags {tag!r} and {other!r} repeat the same subject; "
                            "标签规则 4 forbids synonym stacking"
                        ),
                    )
                )
    return problems


def _text_hygiene(draft: Draft) -> list[ReviewProblem]:
    """The 成稿 carries no link and no named source (来源与引用规则 1-5)."""
    problems: list[ReviewProblem] = []
    text = "\n".join([draft.title, draft.content, draft.brief, *draft.tags])
    if _LINK_RE.search(text) or _MD_LINK_RE.search(text):
        problems.append(
            ReviewProblem(
                type="format",
                severity="high",
                origin="rule",
                detail=(
                    "the 成稿 contains a link; 来源与引用规则 1 allows no link in "
                    "the output"
                ),
            )
        )
    for match in _CITATION_RE.finditer(text):
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail=(
                    "source attribution in the 成稿: "
                    f"{match.group(0).strip()!r} (来源与引用规则 5)"
                ),
            )
        )
    if _MD_BLOCK_RE.search(draft.content):
        problems.append(
            ReviewProblem(
                type="format",
                severity="medium",
                origin="rule",
                detail="content contains Markdown structure; 输出规则 requires plain text",
            )
        )
    return problems


def _facts(
    draft: Draft, event: Event, hits: list[SearchHit]
) -> list[ReviewProblem]:
    """Pipeline rule: flag numbers in the prose that appear nowhere in the material.

    The headline and the brief are published too — the brief reaches the digest
    — so they are scanned with the article. Tags are excluded: a tag is a label,
    not a claim, and a tag like "2级地震" would otherwise fail for a shape the tag
    rules already reject. This cannot prove a sentence is true; it can prove a
    number was not in the input, which is the one check that catches a model
    computing a total, converting a currency or inventing a figure.
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
                            "立场与表达规则 2/3 name this as the false balance to avoid"
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
