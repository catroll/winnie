"""Read a yearly RRULE that a Chinese calendar provider writes in lunar terms.

The phone stores a lunar birthday as a Gregorian ``DTSTART`` for one occurrence
plus an RRULE whose ``BYMONTH``/``BYMONTHDAY`` are the **lunar** month and day.
That is a private convention layered on a standard field: ``dateutil.rrule``
reads it as Gregorian and expands it a month early, with no error. Ten of the
eleven yearly birthday rows in the owner's database are written this way, and
`琪琪的生日` is a genuinely Gregorian row, so the decoder cannot assume that
"the rule does not match its start" means lunar.

The convention is detectable from the row itself:

- if ``(BYMONTH, BYMONTHDAY)`` equals the DTSTART's solar month and day, the
  rule is Gregorian and is left to RFC 5545;
- if it equals the DTSTART date's lunar month and day, the rule is lunar and is
  expanded through :mod:`borax`'s lunar calendar;
- anything else falls back to RFC 5545 as well, because guessing would be worse
  than the old behaviour.

Only ``FREQ=YEARLY`` with one ``BYMONTH`` and one ``BYMONTHDAY`` is decoded.
The year in the rule is the **lunar** year, which starts at Chinese New Year:
`舅舅生日` starts on 2012-01-14 (still lunar year 2011) and its next occurrence
is 2027-01-28, not 2026-12-21.

A lunar month has 29 or 30 days, so a birthday on the 30th does not occur in
every lunar year. It falls back to the 29th, the usual family convention, rather
than disappearing for that year.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Iterator

from borax.calendars.lunardate import LunarDate

logger = logging.getLogger(__name__)

# borax's conversion table covers 1900-2100. A birthday outside it is not a
# case this deployment has, and an out-of-range year falls back to RFC 5545
# rather than raising in a calendar read.
MIN_LUNAR_YEAR = 1900
MAX_LUNAR_YEAR = 2100

_DECODABLE = {"FREQ", "INTERVAL", "WKST", "BYMONTH", "BYMONTHDAY", "UNTIL", "COUNT"}


@dataclass(frozen=True)
class LunarYearly:
    """A yearly rule whose ``BYMONTH``/``BYMONTHDAY`` are lunar."""

    month: int
    day: int
    interval: int = 1
    count: int | None = None
    until: datetime | None = None  # naive UTC


def detect(rule: str, solar_start: date) -> LunarYearly | None:
    """Return the lunar reading of ``rule`` for an event starting on ``solar_start``.

    ``None`` means the rule is Gregorian, is not a shape this decoder
    understands, or its date is outside the lunar table; the caller keeps the
    RFC 5545 expansion.
    """
    parsed = _parse(rule)
    if parsed is None:
        return None
    if (parsed.month, parsed.day) == (solar_start.month, solar_start.day):
        return None
    try:
        lunar = LunarDate.from_solar_date(
            solar_start.year, solar_start.month, solar_start.day
        )
    except Exception:  # noqa: BLE001 - outside the 1900-2100 table
        return None
    if (parsed.month, parsed.day) != (lunar.month, lunar.day):
        return None
    return parsed


def series(rule: LunarYearly, local_start: datetime) -> Iterator[datetime]:
    """Occurrences of a lunar yearly rule, as local aware datetimes, in order.

    The first occurrence is the stored start itself; later ones keep its wall
    clock time and its duration is applied by the caller. The generator stops
    after ``count`` occurrences or past ``until``, and never walks beyond the
    lunar table.
    """
    try:
        lunar_start = LunarDate.from_solar_date(
            local_start.year, local_start.month, local_start.day
        )
    except Exception:  # noqa: BLE001 - outside the 1900-2100 table
        return
    year = max(lunar_start.year, MIN_LUNAR_YEAR)
    emitted = 0
    while year <= MAX_LUNAR_YEAR:
        solar = _solar(year, rule.month, rule.day)
        if solar is not None:
            moment = local_start.replace(
                year=solar.year, month=solar.month, day=solar.day
            )
            if moment >= local_start and not _past(rule.until, moment):
                yield moment
                emitted += 1
                if rule.count is not None and emitted >= rule.count:
                    return
        year += rule.interval


def _solar(year: int, month: int, day: int) -> date | None:
    """The solar date of a lunar month/day, falling back 30 -> 29."""
    candidates = (day, 29) if day == 30 else (day,)
    for candidate in candidates:
        try:
            return LunarDate(year, month, candidate).to_solar_date()
        except Exception:  # noqa: BLE001 - a short month or the table's edge
            continue
    logger.debug("lunar %s-%s-%s does not exist", year, month, day)
    return None


def _past(until: datetime | None, moment: datetime) -> bool:
    if until is None:
        return False
    return moment.astimezone(UTC).replace(tzinfo=None) > until


def _parse(rule: str) -> LunarYearly | None:
    fields: dict[str, str] = {}
    for part in rule.split(";"):
        part = part.strip()
        if not part:
            continue
        key, separator, value = part.partition("=")
        if not separator:
            return None
        key = key.strip().upper()
        if key in fields:
            # Two BYMONTH values is not a birthday; dateutil handles it.
            return None
        fields[key] = value.strip()
    if set(fields) - _DECODABLE:
        return None
    if fields.get("FREQ", "").upper() != "YEARLY":
        return None

    month = _int(fields.get("BYMONTH"), 1, 12)
    day = _int(fields.get("BYMONTHDAY"), 1, 30)
    interval = _int(fields.get("INTERVAL", "1"), 1, 1000)
    if month is None or day is None or interval is None:
        return None

    count = None
    if "COUNT" in fields:
        count = _int(fields["COUNT"], 1, 100_000)
        if count is None:
            return None

    until = None
    if "UNTIL" in fields:
        until = _until(fields["UNTIL"])
        if until is None:
            return None

    return LunarYearly(month=month, day=day, interval=interval, count=count, until=until)


def _int(value: str | None, low: int, high: int) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not text.isdigit():
        # Rejects ",", "-1" (last day) and anything else this decoder does not
        # mean; those rules stay with dateutil.
        return None
    number = int(text)
    return number if low <= number <= high else None


def _until(value: str) -> datetime | None:
    """An RRULE UNTIL as naive UTC.

    Android writes `...Z`; the naive forms are treated as UTC too, which is
    what the provider means by them. An unparseable UNTIL rejects the whole
    rule rather than expanding a bounded series as if it were unbounded.
    """
    text = value.strip()
    for shape in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(text, shape)
        except ValueError:
            continue
    return None
