from __future__ import annotations

from winnie_index.chunker import chunk_markdown


def test_headings_become_the_citation_path():
    text = "# 一级\n\n正文一\n\n## 二级\n\n正文二\n"
    chunks, truncated = chunk_markdown(text, max_tokens=50)
    assert not truncated
    assert [chunk.heading for chunk in chunks] == ["一级", "一级 > 二级"]
    assert chunks[0].start_line == 3
    assert chunks[1].start_line == 7


def test_paragraphs_stay_whole_while_they_fit():
    text = "段落一\n\n段落二\n"
    chunks, _ = chunk_markdown(text, max_tokens=50)
    assert len(chunks) == 1
    assert "段落一" in chunks[0].text and "段落二" in chunks[0].text


def test_code_fence_is_not_split():
    text = "```\nline1\nline2\nline3\n```\n"
    chunks, _ = chunk_markdown(text, max_tokens=50)
    assert len(chunks) == 1
    assert "line1" in chunks[0].text and "line3" in chunks[0].text


def test_oversized_block_splits_with_line_ranges():
    text = "\n\n".join(f"paragraph {index} " + "x" * 200 for index in range(6))
    chunks, _ = chunk_markdown(text, max_tokens=30, overlap=5)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.start_line <= chunk.end_line
    assert chunks[0].start_line == 1


def test_chunk_cap_marks_truncation():
    text = "\n\n".join(f"p{index} " + "y" * 300 for index in range(10))
    chunks, truncated = chunk_markdown(text, max_tokens=20, max_chunks=2)
    assert truncated
    assert len(chunks) == 2


def test_empty_text_yields_no_chunks():
    chunks, truncated = chunk_markdown("   \n\n")
    assert chunks == []
    assert not truncated
