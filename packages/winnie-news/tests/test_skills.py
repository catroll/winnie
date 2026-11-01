"""The prompts quote the plugin files; these tests pin the quoted bytes.

A paraphrase in a prompt is invisible in review and changes behaviour silently.
The prompt layer therefore loads the vendored ``SKILL.md`` files, and this test
fails when one of them is edited instead of replaced with the plugin's own file.
"""

from __future__ import annotations

import hashlib

from winnie_news.skills import SKILL_FILES, quoted_skills, skill_text

# sha256 of each SKILL.md as extracted from the owner's plugin archives; keep in
# sync with winnie_news/prompts/README.md.
EXPECTED_SHA256 = {
    "news-drafting": "2ea5f89666baa19d24435dee830c4fd89ce3dc9cd63c08db76c068a2a914e02c",
    "hot-news": "8e61adb03a2b213905d7c2cdaac27a5913072a9b135287cf1c49045c5251fecf",
}


def test_the_vendored_files_match_the_plugin_files() -> None:
    for skill, expected in EXPECTED_SHA256.items():
        digest = hashlib.sha256(skill_text(skill).encode("utf-8")).hexdigest()
        assert digest == expected, (
            f"{SKILL_FILES[skill]} changed; copy the plugin's SKILL.md again, then "
            "update EXPECTED_SHA256 and prompts/README.md"
        )


def test_quoted_skills_embed_each_file_verbatim() -> None:
    block = quoted_skills()
    for skill in SKILL_FILES:
        text = skill_text(skill)
        assert f'<skill name="{skill}">\n{text}\n</skill>' in block


def test_both_skills_are_quoted_in_a_stable_order() -> None:
    block = quoted_skills()
    assert block.index('name="news-drafting"') < block.index('name="hot-news"')
