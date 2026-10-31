"""Turn bytes into text, or refuse them.

There is no parser to choose here, but an extension is not proof of content: a
`.ts` file can be a video segment and a `.md` can be a binary blob. The gate is
deliberately conservative, and everything it rejects is recorded as sealed
rather than silently indexed as mojibake.
"""

from __future__ import annotations

import re

# utf-8-sig also accepts plain UTF-8, so a BOM never reaches the index.
_ENCODINGS = ("utf-8-sig", "gb18030")
_NUL_WINDOW = 8192
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class BinaryFile(ValueError):
    """The bytes do not look like text."""


class DecodeError(ValueError):
    """The bytes look like text but no supported encoding decodes them."""


def decode_bytes(data: bytes) -> tuple[str, str]:
    """Return ``(text, encoding)``; raise for binary or undecodable input."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        raise BinaryFile("UTF-16 byte order mark")
    if b"\x00" in data[:_NUL_WINDOW]:
        raise BinaryFile("NUL byte in the first 8 KB")

    for encoding in _ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        # A short binary payload can survive a codec; count control characters.
        controls = len(_CONTROL.findall(text[:8192]))
        if controls > max(8, len(text[:8192]) // 20):
            raise BinaryFile("too many control characters for text")
        return text.replace("\r\n", "\n").replace("\r", "\n"), encoding

    raise DecodeError("not decodable as " + "/".join(_ENCODINGS))
