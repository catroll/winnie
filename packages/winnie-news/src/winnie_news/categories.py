"""The four sections a draft and a digest are organised into.

Both prompt contracts this pipeline implements name the same sections: the
draft is assigned one, and the digest groups drafts by them in this order. The
set is fixed for the same reason the event taxonomy is fixed — a model that
invents "科技/互联网" makes two runs impossible to compare or concatenate.

``derive_category`` is the mechanical fallback used when the writer returns no
usable category. It can only see the event type family and the country-level
locations, so it is deliberately coarse: a foreign country means 国际要闻, and
everything else that is not finance or sport stays 国内要闻.
"""

from __future__ import annotations

from typing import Any, Iterable

CATEGORIES: tuple[str, ...] = ("国内要闻", "国际要闻", "财经科技", "体育文娱")

# The short labels the one-line brief uses: the full name would crowd a 40
# character line.
SHORT_LABELS: dict[str, str] = {
    "国内要闻": "国内",
    "国际要闻": "国际",
    "财经科技": "财经",
    "体育文娱": "体育/文娱",
}

_ALIASES: dict[str, str] = {
    "国内": "国内要闻",
    "国内新闻": "国内要闻",
    "中国": "国内要闻",
    "china": "国内要闻",
    "domestic": "国内要闻",
    "国际": "国际要闻",
    "国际新闻": "国际要闻",
    "国际要闻": "国际要闻",
    "world": "国际要闻",
    "international": "国际要闻",
    "global": "国际要闻",
    "财经": "财经科技",
    "财经科技": "财经科技",
    "科技": "财经科技",
    "商业": "财经科技",
    "finance": "财经科技",
    "business": "财经科技",
    "tech": "财经科技",
    "technology": "财经科技",
    "体育": "体育文娱",
    "文娱": "体育文娱",
    "体育文娱": "体育文娱",
    "娱乐": "体育文娱",
    "sports": "体育文娱",
    "entertainment": "体育文娱",
    "culture": "体育文娱",
}

# Names that mean "China" when they appear as a country-level location. A
# foreign country outside this set is what makes the fallback pick 国际要闻.
_CHINA_NAMES = {"中国", "中华人民共和国", "中国大陆", "china", "prc"}


def normalize_category(raw: Any) -> str:
    """Map a free label to one of the four sections, or return ``""``."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text in CATEGORIES:
        return text
    lowered = text.lower()
    if lowered in _ALIASES:
        return _ALIASES[lowered]
    for alias, category in _ALIASES.items():
        if alias in lowered:
            return category
    return ""


def derive_category(event_type: str, locations: Iterable[Any] = ()) -> str:
    """Pick a section from the event type and its country-level locations.

    ``locations`` may be ``NamedEntity`` objects or plain strings; only an entry
    that names a country can separate domestic from international, so a bare
    city name is not guessed at.
    """
    family = str(event_type or "").split(".", 1)[0]
    if family == "sports":
        return "体育文娱"
    if family in ("tech", "corporate", "economy"):
        return "财经科技"
    for location in locations:
        name = str(getattr(location, "name", location) or "").strip()
        level = str(getattr(location, "level", "") or "").strip().lower()
        if level == "country" and name.lower() not in _CHINA_NAMES and name:
            return "国际要闻"
    return "国内要闻"
