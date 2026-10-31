from __future__ import annotations

from datetime import datetime, timezone

import pytest

from winnie_assistant.memory import MemoryStore, tokenize


def test_tokenizer_handles_latin_and_chinese() -> None:
    tokens = tokenize("Python asyncio 帮助 处理 I/O")
    assert "python" in tokens
    assert "asyncio" in tokens
    assert "帮助" in tokens
    assert "处理" in tokens


def test_append_dedupes_and_searches(tmp_path) -> None:
    memory = MemoryStore(tmp_path)
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)

    assert memory.append("preferences", "用户喜欢简短回答。", source="chat:1", now=now)
    assert not memory.append("preferences", "用户喜欢简短回答。", source="chat:2", now=now)
    assert memory.append("projects", "正在做 Winnie 助理。", source="chat:1", now=now)

    files = {file.relpath: file for file in memory.files()}
    assert set(files) == {"preferences.md", "projects.md"}
    assert files["preferences.md"].pinned is True
    assert files["projects.md"].pinned is False

    hits = memory.search("简短回答")
    assert hits
    assert hits[0].entry.category == "preferences"
    assert hits[0].entry.text == "用户喜欢简短回答。"
    assert hits[0].entry.source == "chat:1"
    assert "简短回答" in hits[0].snippet


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
    assert memory.search("简短回答")

    (tmp_path / "preferences.md").write_text(
        "---\ncategory: preferences\npinned: true\n---\n"
        "# Preferences\n"
        "- [2026-11-01] 用户喜欢喝茶 <!-- source: manual -->\n",
        encoding="utf-8",
    )

    assert memory.search("简短回答") == []
    hits = memory.search("喝茶")
    assert hits[0].entry.text == "用户喜欢喝茶"
    assert hits[0].entry.source == "manual"


def test_extra_roots_are_read_only_sources(tmp_path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "family.md").write_text("# Family\n- 妻子不吃香菜\n", encoding="utf-8")

    memory = MemoryStore(tmp_path / "memory", extra_roots=(notes,))
    hits = memory.search("香菜")
    assert hits
    assert hits[0].entry.relpath == "notes/family.md"
    assert not (tmp_path / "memory" / "family.md").exists()


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
