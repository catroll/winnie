from __future__ import annotations

from pathlib import Path

import pytest
from winnie_index import db
from winnie_index.config import Config
from winnie_index.scan import scan
from winnie_index.search import get_document, search, search_debug, status


@pytest.fixture()
def conn(config: Config):
    connection = db.connect(config.db_path)
    db.ensure_schema(connection)
    yield connection
    connection.close()


def test_scan_indexes_text_and_seals_binary(conn, config: Config):
    stats = scan(conn, config)
    assert stats.added == 4  # project.md, todo.txt, copy.md, binary.md
    assert stats.sealed == 1  # binary.md
    assert stats.failed == 0
    assert stats.chunks >= 2


def test_scan_is_incremental(conn, config: Config):
    scan(conn, config)
    again = scan(conn, config)
    assert again.added == 0
    assert again.unchanged == 4
    assert again.bytes_read == 0


def test_search_finds_chinese_terms(conn, config: Config):
    scan(conn, config)
    hits = search(conn, config, "决定")
    assert hits
    assert hits[0]["path"] == "project.md"
    assert hits[0]["start_line"] == 3


def test_search_matches_two_character_words(conn, config: Config):
    scan(conn, config)
    hits = search(conn, config, "部署")
    assert hits
    assert hits[0]["heading"] == "项目记录 > 部署"


def test_duplicates_collapse_into_one_result(conn, config: Config):
    scan(conn, config)
    hits = search(conn, config, "Qdrant")
    assert len(hits) == 1
    assert hits[0]["also_at"]


def test_edit_is_picked_up_by_the_next_sweep(conn, config: Config, tree: Path):
    scan(conn, config)
    target = tree / "roots" / "notes" / "todo.txt"
    target.write_text("明天把向量索引接上\n", encoding="utf-8")
    stats = scan(conn, config)
    assert stats.changed == 1
    hits = search(conn, config, "向量索引")
    assert hits and hits[0]["path"] == "todo.txt"


def test_rename_keeps_chunks(conn, config: Config, tree: Path):
    scan(conn, config)
    before = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    source = tree / "roots" / "notes" / "todo.txt"
    target = tree / "roots" / "notes" / "todo2.txt"
    source.rename(target)
    stats = scan(conn, config)
    after = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert stats.renamed == 1
    assert stats.deleted == 0
    assert after == before
    assert search(conn, config, "明天")[0]["path"] == "todo2.txt"


def test_delete_removes_hits(conn, config: Config, tree: Path):
    scan(conn, config)
    (tree / "roots" / "notes" / "todo.txt").unlink()
    stats = scan(conn, config)
    assert stats.deleted == 1
    assert search(conn, config, "明天") == []


def test_a_deleted_file_that_reappears_is_indexed_again(conn, config: Config, tree: Path):
    scan(conn, config)
    target = tree / "roots" / "notes" / "todo.txt"
    target.unlink()
    assert scan(conn, config).deleted == 1
    target.write_text("明天把向量索引接上\n", encoding="utf-8")
    stats = scan(conn, config)
    assert stats.added == 1
    assert stats.deleted == 0
    assert search(conn, config, "向量索引")[0]["path"] == "todo.txt"
    row = conn.execute(
        "SELECT state, deleted_at FROM docs WHERE rel_path = 'todo.txt'"
    ).fetchone()
    assert row["state"] == "indexed"
    assert row["deleted_at"] is None


def test_a_tombstone_is_not_deleted_again_by_the_next_sweep(conn, config: Config, tree: Path):
    scan(conn, config)
    (tree / "roots" / "notes" / "todo.txt").unlink()
    assert scan(conn, config).deleted == 1
    again = scan(conn, config)
    assert again.deleted == 0
    assert again.added == 0


def test_a_rename_never_lands_on_a_tombstoned_path(conn, config: Config, tree: Path):
    scan(conn, config)
    notes = tree / "roots" / "notes"
    old = notes / "a.md"
    old.write_text("# 甲\n\n部署一\n", encoding="utf-8")
    scan(conn, config)
    old.unlink()
    assert scan(conn, config).deleted == 1
    moved = notes / "b.md"
    moved.write_text("# 乙\n\n部署二\n", encoding="utf-8")
    scan(conn, config)
    moved.rename(old)
    stats = scan(conn, config)
    assert stats.added == 1  # the tombstone at a.md is re-indexed in place
    assert stats.deleted == 1  # b.md is gone
    hits = search(conn, config, "部署二")
    assert hits and hits[0]["path"] == "a.md"
    assert search(conn, config, "部署一") == []


def test_touch_without_content_change_does_not_rechunk(conn, config: Config, tree: Path):
    scan(conn, config)
    target = tree / "roots" / "notes" / "todo.txt"
    target.touch()
    stats = scan(conn, config)
    assert stats.changed == 1
    assert stats.chunks == 0


def test_get_document_revalidates_after_an_out_of_band_edit(conn, config: Config, tree: Path):
    scan(conn, config)
    target = tree / "roots" / "notes" / "todo.txt"
    target.write_text("这句还没进索引\n", encoding="utf-8")
    document = get_document(conn, config, "todo.txt")
    assert "这句还没进索引" in str(document["text"])
    assert document["fresh"] is False
    # the inline refresh is durable: the next search already sees the new text
    assert search(conn, config, "这句还没进索引")


def test_get_document_returns_a_line_window(conn, config: Config):
    scan(conn, config)
    document = get_document(conn, config, "project.md", from_line=1, lines=2)
    assert str(document["text"]).splitlines()[0] == "# 项目记录"


def test_status_reports_roots_and_states(conn, config: Config):
    scan(conn, config)
    data = status(conn, config)
    assert data["documents"].get("indexed") == 3
    assert data["documents"].get("sealed") == 1
    assert data["chunks"] > 0
    assert {root["name"] for root in data["roots"]} == {"notes", "docs"}


def test_unsupported_extension_is_not_seen(conn, config: Config):
    entries = scan(conn, config)
    rows = conn.execute("SELECT rel_path FROM docs WHERE rel_path LIKE '%.pdf'").fetchall()
    assert rows == []
    assert entries.seen == 4


def test_three_character_chinese_word_matches(conn, config: Config, tree: Path):
    (tree / "roots" / "notes" / "kb.md").write_text(
        "# 知识库\n\n这是知识库的说明。\n", encoding="utf-8"
    )
    scan(conn, config)
    hits = search(conn, config, "知识库")
    assert hits
    assert hits[0]["path"] == "kb.md"


def test_two_chunks_of_one_document_can_both_appear(conn, config: Config, tree: Path):
    (tree / "roots" / "notes" / "two.md").write_text(
        "# 甲\n\n部署一\n\n# 乙\n\n部署二\n", encoding="utf-8"
    )
    scan(conn, config)
    hits = [hit for hit in search(conn, config, "部署") if hit["path"] == "two.md"]
    assert len(hits) == 2


def test_duplicate_alias_is_never_the_same_path(conn, config: Config):
    scan(conn, config)
    hits = search(conn, config, "Qdrant")
    assert hits[0]["also_at"]
    assert hits[0]["path"] not in hits[0]["also_at"]


def test_line_ending_change_does_not_rechunk(conn, config: Config, tree: Path):
    scan(conn, config)
    before = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    target = tree / "roots" / "notes" / "todo.txt"
    target.write_bytes("明天把检索接上\r\n".encode("utf-8"))
    stats = scan(conn, config)
    after = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert stats.changed == 1
    assert stats.chunks == 0
    assert after == before


def test_debug_search_explains_a_hit(conn, config: Config):
    scan(conn, config)
    result = search_debug(conn, config, "决定")
    assert result["expression"] == '"决定"'
    assert result["terms"] == ["决定"]
    assert result["hits"]
    first = result["hits"][0]
    assert first["path"] == "project.md"
    assert first["start_line"] == 3
    assert "我们决定" in first["text"]
    assert result["files"][0]["path"] == "project.md"


def test_debug_search_keeps_every_chunk_of_one_document(conn, config: Config, tree: Path):
    (tree / "roots" / "notes" / "two.md").write_text(
        "# 甲\n\n部署一\n\n# 乙\n\n部署二\n", encoding="utf-8"
    )
    scan(conn, config)
    result = search_debug(conn, config, "部署")
    paths = [hit["path"] for hit in result["hits"]]
    assert paths.count("two.md") == 2


def test_debug_search_without_a_match_explains_itself(conn, config: Config):
    scan(conn, config)
    result = search_debug(conn, config, "zzz-not-present")
    assert result["hits"] == []
    assert result["hint"]


def test_debug_search_reports_the_expression_for_a_blank_query(conn, config: Config):
    scan(conn, config)
    result = search_debug(conn, config, " ，。 ")
    assert result["expression"] is None
    assert "no indexable terms" in result["hint"]


def test_debug_search_honours_the_root_filter(conn, config: Config):
    scan(conn, config)
    result = search_debug(conn, config, "Qdrant", roots=["docs"])
    assert {hit["root"] for hit in result["hits"]} == {"docs"}
