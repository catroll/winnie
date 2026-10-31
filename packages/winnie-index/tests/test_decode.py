from __future__ import annotations

import pytest
from winnie_index.decode import BinaryFile, DecodeError, decode_bytes


def test_utf8_with_bom_is_stripped():
    text, encoding = decode_bytes("\ufeff你好".encode("utf-8"))
    assert text == "你好"
    assert encoding == "utf-8-sig"


def test_gbk_is_decoded_rather_than_mojibake():
    text, encoding = decode_bytes("部署完成".encode("gb18030"))
    assert text == "部署完成"
    assert encoding == "gb18030"


def test_crlf_is_normalised():
    text, _ = decode_bytes(b"a\r\nb\rc")
    assert text == "a\nb\nc"


def test_nul_byte_is_binary():
    with pytest.raises(BinaryFile):
        decode_bytes(b"text\x00more")


def test_high_control_character_ratio_is_binary():
    with pytest.raises(BinaryFile):
        decode_bytes(b"\x01" * 100)


def test_utf16_is_rejected_as_binary():
    with pytest.raises(BinaryFile):
        decode_bytes("你好".encode("utf-16"))
