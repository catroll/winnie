"""Time normalisation for the news pipeline.

Every timestamp in a schema is an ISO 8601 string. Sources disagree about that:
RSS uses RFC 2822, tophub emits ``YYYY-MM-DD HH:MM``, some APIs emit a time of
day with the date supplied out of band. Only the source date is trusted; an
unparseable value becomes ``None`` instead of a guess, because a wrong
publication time silently breaks the event window used by aggregation.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

logger = logging.getLogger(__name__)

# Winnie runs for a user in one timezone; the Chinese sources it reads are all
# in Asia/Shanghai, and a fixed offset keeps the pipeline independent of tzdata.
SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")

TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)
TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def now() -> datetime:
    return datetime.now(SHANGHAI)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def iso(moment: datetime) -> str:
    """Render a datetime in Asia/Shanghai; naive values are assumed local."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=SHANGHAI)
    return moment.astimezone(SHANGHAI).isoformat(timespec="seconds")


def today() -> str:
    return now().strftime("%Y-%m-%d")


def normalize_time(value: Any, *, date: str | None = None) -> str | None:
    """Normalise a source timestamp to ISO 8601, or ``None``.

    Accepts ``YYYY-MM-DD HH:MM[:SS]``, ``YYYY-MM-DDTHH:MM[:SS][+08:00]``,
    ``YYYY/MM/DD``, RFC 2822 (RSS), a date alone, and a time alone when
    ``date`` supplies the day.
    """
    text = str(value or "").strip()
    if not text:
        return None

    if TIME_ONLY_RE.fullmatch(text):
        if not date:
            logger.warning("time %r has no date and none was supplied", value)
            return None
        text = f"{date} {text}"

    text = text.replace("/", "-")
    if DATE_ONLY_RE.fullmatch(text):
        return text

    # Python's fromisoformat accepts "Z" from 3.11, but the sources still send
    # it inconsistently; normalising here keeps one code path.
    if text.endswith("Z") and "T" in text:
        text = text[:-1] + "+00:00"

    candidates = [text]
    if " " in text:
        candidates.append(text.replace(" ", "T", 1))

    for candidate in candidates:
        for fmt in TIME_FORMATS:
            try:
                return iso(datetime.strptime(candidate, fmt))
            except ValueError:
                continue
        try:
            return iso(datetime.fromisoformat(candidate))
        except ValueError:
            continue

    try:
        return iso(parsedate_to_datetime(text))
    except (TypeError, ValueError, OverflowError):
        pass

    logger.warning("unrecognised time %r, storing null", value)
    return None
