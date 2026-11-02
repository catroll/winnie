from __future__ import annotations

from datetime import datetime, timezone

import pytest

from winnie_assistant.memory import MemoryStore


def test_append_dedupes_and_lists_files(tmp_path) -> None:
    memory = MemoryStore(tmp_path, label="profile")
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)

    assert memory.append("preferences", "用户喜欢简短回答。", source="chat:1", now=now)
    assert not memory.append("preferences", "用户喜欢简短回答。", source="chat:2", now=now)
    assert memory.append("projects", "正在做 Winnie 助理。", source="chat:1", now=now)

    files = {file.relpath: file for file in memory.files()}
    assert set(files) == {"profile/preferences.md", "profile/projects.md"}
    assert files["profile/preferences.md"].pinned is True
    assert files["profile/projects.md"].pinned is False

    entries = {(entry.category, entry.text) for entry in memory.entries}
    assert ("preferences", "用户喜欢简短回答。") in entries
    assert ("projects", "正在做 Winnie 助理。") in entries
    preference = next(e for e in memory.entries if e.category == "preferences")
    assert preference.source == "chat:1"
    assert preference.entry_date == "2026-11-01"


def test_pinned_digest_excludes_unpinned_categories(tmp_path) -> None:
    memory = MemoryStore(tmp_path)
    memory.append("preferences", "回答先给结论。", source="chat:1")
    memory.append("projects", "Winnie 助理。", source="chat:1")

    digest = memory.digest()
    assert "回答先给结论。" in digest
    assert "Winnie 助理。" not in digest


def test_manual_edits_are_obeyed_on_refresh(tmp_path) -> None:
    memory = MemoryStore(tmp_path)
    memory.append("preferences", "用户喜欢简短回答。", source="chat:1")
    assert any(entry.text == "用户喜欢简短回答。" for entry in memory.entries)

    (tmp_path / "preferences.md").write_text(
        "---\ncategory: preferences\npinned: true\n---\n"
        "# Preferences\n"
        "- [2026-11-01] 用户喜欢喝茶 <!-- source: manual -->\n",
        encoding="utf-8",
    )

    entries = memory.entries
    assert [entry.text for entry in entries] == ["用户喜欢喝茶"]
    assert entries[0].source == "manual"


def test_pinned_front_matter_can_pin_a_usually_unpinned_file(tmp_path) -> None:
    memory = MemoryStore(tmp_path)
    (tmp_path / "projects.md").write_text(
        "---\ncategory: projects\npinned: true\n---\n"
        "# Projects\n"
        "- [2026-11-01] Winnie 助理\n",
        encoding="utf-8",
    )
    assert "Winnie 助理" in memory.digest()


def test_digest_is_bounded(tmp_path) -> None:
    memory = MemoryStore(tmp_path, digest_chars=60)
    for index in range(6):
        memory.append(
            "preferences",
            f"第 {index} 条偏好是一句很长的话，用来把摘要推到上限之外。",
            source="chat:1",
        )
    digest = memory.digest()
    assert len(digest) < 240
    assert "omitted" in digest


def test_unknown_category_is_rejected(tmp_path) -> None:
    memory = MemoryStore(tmp_path)
    with pytest.raises(ValueError, match="nonsense"):
        memory.append("nonsense", "x", source="chat:1")


def test_procedures_is_pinned_even_when_the_file_says_otherwise(tmp_path) -> None:
    """A file written before the category was pinned must still reach the prompt.

    On 2026-11-01 the owner's default-city rule was written to procedures and
    the file carried `pinned: false`; honouring that front matter meant the rule
    was in no prompt at all.
    """
    memory = MemoryStore(tmp_path)
    (tmp_path / "procedures.md").write_text(
        "---\ncategory: procedures\npinned: false\n---\n"
        "# Procedures\n- [2026-11-01] 没有指定位置时默认查询武汉。\n",
        encoding="utf-8",
    )

    assert "默认查询武汉" in memory.digest()
    assert memory.files()[0].pinned is True


def test_recall_matches_a_paraphrase_of_a_stored_note(tmp_path) -> None:
    memory = MemoryStore(tmp_path)
    memory.append(
        "projects",
        "Winnie 的天气查询在未指定地点时默认查询武汉的天气。",
        source="chat:1",
    )

    recalled = memory.recall("天气默认查哪里？")
    assert "默认查询武汉" in recalled
    assert "profile/projects.md" in recalled


def test_recall_skips_pinned_entries_because_the_digest_has_them(tmp_path) -> None:
    memory = MemoryStore(tmp_path)
    memory.append("preferences", "用户喜欢先看结论。", source="chat:1")

    assert "先看结论" in memory.digest()
    assert memory.recall("我喜欢先看结论吗？") == ""


def test_recall_ignores_a_message_with_nothing_in_common(tmp_path) -> None:
    memory = MemoryStore(tmp_path)
    memory.append("people", "孙秀是胡昂的联系人。", source="chat:1")

    assert memory.recall("帮我写个新闻稿") == ""


def test_recall_keeps_the_budget(tmp_path) -> None:
    memory = MemoryStore(tmp_path)
    for index in range(8):
        memory.append("people", f"同事 {index} 的联系方式在通讯录里。", source="chat:1")

    recalled = memory.recall("同事的联系方式", limit=3, chars=80)
    assert len(recalled.splitlines()) <= 3
    assert len(recalled) <= 80


def test_recall_uses_the_conversation_to_resolve_a_bare_follow_up(tmp_path) -> None:
    """A follow-up says nothing on its own; the exchange carries the subject."""
    memory = MemoryStore(tmp_path)
    memory.append("people", "图图是胡昂需要接送的孩子。", source="chat:1")

    assert memory.recall("那几点？") == ""
    recalled = memory.recall("那几点？", context=["明天带图图去上课是几点？"])
    assert "图图" in recalled



def test_the_distiller_is_told_not_to_store_a_failed_lookup() -> None:
    """A negative result became a fact once; the prompt now rules it out.

    On 2026-11-01 "not found" was distilled from a failed search, and the next
    birthday question was answered from it without a tool call. This is the
    executable half of the guard; the other half is the skill instruction that
    points the model at the data.
    """
    from winnie_assistant.memory_builder import SYSTEM_PROMPT

    assert "Never store a failed lookup" in SYSTEM_PROMPT


def test_entry_lines_are_file_lines_not_body_lines(tmp_path) -> None:
    """A citation that is four lines off is worse than no citation."""
    (tmp_path / "preferences.md").write_text(
        "---\n"
        'category: "preferences"\n'
        "pinned: true\n"
        'updated: "2026-11-01"\n'
        "---\n"
        "# Preferences\n"
        "\n"
        "- [2026-11-01] 第一条。\n"
        "- [2026-11-01] 第二条。\n",
        encoding="utf-8",
    )

    entries = MemoryStore(tmp_path).entries

    assert [entry.line for entry in entries] == [8, 9]
    assert entries[0].text == "第一条。"


def test_a_punctuation_variant_is_the_same_fact(tmp_path) -> None:
    """The distiller produced this pair; exact-string dedupe kept both."""
    memory = MemoryStore(tmp_path)
    first = "用户未指定地点时，若可获取经纬度，则按该经纬度查询当地天气；否则默认查询武汉的天气。"
    variant = "用户未指定地点时，若可获取经纬度则按该经纬度查询当地天气，否则默认查询武汉的天气。"

    assert memory.append("procedures", first, source="chat:1")
    assert not memory.append("procedures", variant, source="chat:2")
    assert len([e for e in memory.entries if "默认查询武汉" in e.text]) == 1
