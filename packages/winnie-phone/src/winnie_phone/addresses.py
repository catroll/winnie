"""Phone-number normalisation for SMS grouping and filtering.

The provider stores the same sender under different strings depending on
which way the message went: an incoming message often arrives as
``+8613800138000`` while the reply to it is written as ``13800138000``. A
conversation is one thread in the owner's head, so every comparison here is
made on the digits only, with the country code removed when the remainder is
a Chinese mobile number.

An alphanumeric sender id (some carriers and banks use one) is not a phone
number, so it is compared case-insensitively as written rather than reduced to
its few digits.

Only the SMS query paths use this. Calls and contacts keep the raw number the
device uploaded, because those rows are quoted back to the owner as they are.
"""

from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"[^0-9]")
_LETTER = re.compile(r"[A-Za-z]")


def normalize_address(value: str) -> str:
    """Return the form of a sender that two spellings of it share.

    ``+8613800138000`` and ``8613800138000`` both become ``13800138000``;
    ``10086`` and ``1069000000000`` are returned unchanged; an alphanumeric
    sender id such as ``BANK`` becomes ``bank``.
    """
    text = (value or "").strip()
    if not text:
        return ""
    if _LETTER.search(text):
        # Not a phone number: there is no prefix to remove, only case to fold.
        return text.casefold()
    digits = _NON_DIGITS.sub("", text)
    if len(digits) == 13 and digits.startswith("861"):
        return digits[2:]
    # The international dialling prefix form: 0086 + 11-digit mobile.
    if len(digits) == 15 and digits.startswith("00861"):
        return digits[4:]
    return digits
