from __future__ import annotations

from winnie_index.tokenize import count_tokens, fts_query, raw_terms, segments, tokenize


def test_cjk_bigrams_and_ascii_words():
    assert tokenize("Redis 缓存") == ["redis", "缓存"]
    assert tokenize("部署回滚") == ["部署", "署回", "回滚"]


def test_single_cjk_character_is_kept():
    assert tokenize("猫") == ["猫"]


def test_segments_split_on_punctuation_and_spaces():
    assert segments("部署，回滚 策略") == ["部署", "回滚", "策略"]


def test_query_builds_a_phrase_per_segment():
    query = fts_query("今天天气 很好")
    assert query is not None
    assert query.startswith('"今天 天天 天气"')
    assert " AND " in query


def test_two_character_chinese_query_is_one_term():
    assert fts_query("天气") == '"天气"'


def test_empty_query_has_no_expression():
    assert fts_query("   ，。 ") is None


def test_raw_terms_are_surface_terms():
    terms = raw_terms("Qdrant 部署")
    assert "qdrant" in terms
    assert "部署" in terms


def test_count_tokens_counts_cjk_per_character():
    assert count_tokens("中文") == 2
    assert count_tokens("abcd") >= 1
