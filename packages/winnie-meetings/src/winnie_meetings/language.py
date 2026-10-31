"""Cheap per-segment language detection for the meeting pipeline.

The Doubao recording-file response has no reliable per-utterance language, so
the pipeline estimates it from the transcript. Script detection is enough for
the Chinese/English meetings this product targets: it never calls a provider,
it is deterministic, and it keeps short numeric segments aligned with their
neighbour. A real language-ID model can replace this module without changing
the meeting service.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageGuess:
    code: str
    confidence: float


def detect_language(text: str) -> LanguageGuess | None:
    """Return the dominant script language, or None when there is no signal.

    Latin letters, CJK ideographs, kana and hangul are counted. Punctuation,
    digits and whitespace do not vote. A script must hold at least 40% of the
    letters to win, which prevents a Chinese name inside an English sentence
    from flipping the segment.
    """
    cjk = 0
    latin = 0
    kana = 0
    hangul = 0
    for char in text:
        codepoint = ord(char)
        if 0x4E00 <= codepoint <= 0x9FFF:
            cjk += 1
        elif 0x3040 <= codepoint <= 0x30FF:
            kana += 1
        elif 0xAC00 <= codepoint <= 0xD7AF:
            hangul += 1
        elif "a" <= char.lower() <= "z":
            latin += 1

    total = cjk + latin + kana + hangul
    if total < 2:
        return None

    # Japanese uses kanji plus kana; a little kana is decisive because Chinese
    # text does not use it. Korean is almost entirely hangul.
    if kana and kana / total >= 0.1:
        return LanguageGuess("ja", kana / total)
    if hangul and hangul / total >= 0.25:
        return LanguageGuess("ko", hangul / total)

    counts = {"zh": cjk, "en": latin}
    code, count = max(counts.items(), key=lambda item: item[1])
    if count / total < 0.4:
        return None
    return LanguageGuess(code, count / total)
