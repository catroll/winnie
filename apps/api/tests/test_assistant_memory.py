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
