"""The two editor plugins, quoted verbatim into the news prompts.

The files under ``prompts/`` are byte-for-byte copies of the ``SKILL.md`` files
shipped in the owner's ChatGPT plugins (see ``prompts/README.md`` for the
provenance and the sha256 of each). They are the specification of what a draft
and a digest must look like, so they are embedded as files rather than retyped:
a retyped rule is a paraphrase, and a paraphrase is where a requirement quietly
changes.

``tests/test_skills.py`` pins both hashes. When the owner updates a plugin, copy
the new ``SKILL.md`` over the file here and update the hash and the provenance
table in the same change; a failing hash test is the signal that the quoted
rules no longer match what the owner wrote.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

# Plugin name used by the prompt envelope -> vendored file name.
SKILL_FILES: dict[str, str] = {
    "news-drafting": "news_drafting.md",
    "hot-news": "hot_news.md",
}


@lru_cache(maxsize=4)
def skill_text(skill: str) -> str:
    """Return one vendored ``SKILL.md`` exactly as shipped."""
    filename = SKILL_FILES[skill]
    resource = files("winnie_news").joinpath("prompts").joinpath(filename)
    return resource.read_text(encoding="utf-8")


def quoted_skills() -> str:
    """Both skills in one prompt-ready block, verbatim, in a fixed order."""
    blocks = [
        f'<skill name="{name}">\n{skill_text(name)}\n</skill>' for name in SKILL_FILES
    ]
    return "\n\n".join(blocks)
