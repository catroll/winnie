"""Expand a stored RRULE into the occurrences a calendar read needs.

The sync stores one row per `Events` row, with its `RRULE` and `EXDATE`, because
that id is what the write path uses to update or delete the series. A read that
answers "what is on today" cannot work from the base row alone: a weekly class
whose first occurrence was in August has to appear on this week's date, a
birthday stored with its birth year has to appear every year, and neither is
inside the ±window the sync uploads.

Expansion happens at read time, in the caller's window, so the stored row stays
the authoritative series. A rule that cannot be parsed never hides the event:
the base occurrence is returned instead and the failure is logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

logger = logging.getLogger(__name__)

# A daily rule over a year is 365 occurrences; more than this in one window is
# a sign of a broken rule or a window nobody meant to ask for.
MAX_OCCURRENCES_PER_EVENT = 5000


@dataclass(frozen=True)
class Occurrence:
    """One occurrence of an event, as naive UTC instants."""

    starts_at: datetime
    ends_at: datetime | None


def zone(name: str) -> ZoneInfo:
    """The event's zone, or UTC when the stored name is not one.

    `CalendarContract.Events.EVENT_TIMEZONE` is not guaranteed to be an Olson
    id; the client normalises the Java `SimpleTimeZone` spelling and caps the
    length, so anything left that the tz database does not know is treated as
    UTC rather than failing the read.
    """
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo("UTC")


def expand(
    *,
    starts_at: datetime | None,
    ends_at: datetime | None,
    timezone: str,
    rrule: str,
    exdate: str = "",
    since: datetime | None,
    until: datetime | None,
    max_occurrences: int = MAX_OCCURRENCES_PER_EVENT,
) -> list[Occurrence]:
    """Occurrences of one event that start inside ``since``..``until``.

    Both bounds are naive UTC and both are required for expansion: without a
    window the caller asked for "everything the server holds", and inventing one
    here would turn a daily rule into an unbounded loop. With no rule, or with
    no start, the event itself is the single occurrence.
    """
    if starts_at is None:
        return []
    base = Occurrence(starts_at=starts_at, ends_at=ends_at)
    if not rrule.strip() or since is None or until is None:
        return [base] if _inside(base.starts_at, since, until) else []

    tz = zone(timezone)
    local_start = _local(starts_at, tz)
    duration = (ends_at - starts_at) if ends_at is not None else None
    rule, naive = _parse_rule(rrule, local_start)
    if rule is None:
        return [base] if _inside(base.starts_at, since, until) else []

    window_start = _bound(since, tz, naive)
    window_end = _bound(until, tz, naive)
    try:
        occurrences = rule.between(window_start, window_end, inc=True)
    except (ValueError, TypeError) as error:
        logger.warning(
            "calendar rrule cannot be walked, keeping the base occurrence: %r (%s)",
            rrule,
            error,
        )
        return [base] if _inside(base.starts_at, since, until) else []

    excluded = _parse_exdates(exdate, tz)
    result: list[Occurrence] = []
    for starts in occurrences:
        if len(result) >= max_occurrences:
            logger.warning(
                "calendar rrule produced more than %d occurrences in the window; "
                "truncating: %r",
                max_occurrences,
                rrule,
            )
            break
        aware = starts if starts.tzinfo is not None else starts.replace(tzinfo=tz)
        if aware.astimezone(UTC) in excluded:
            continue
        utc_start = _utc(aware)
        if not _inside(utc_start, since, until):
            continue
        result.append(
            Occurrence(
                starts_at=utc_start,
                ends_at=(utc_start + duration) if duration is not None else None,
            )
        )
    return result


def _parse_rule(rrule: str, local_start: datetime):
    """Parse the stored RRULE against the event's own start.

    DTSTART comes from the row because Android stores the rule without one. An
    `UNTIL` written in UTC (`...Z`) against an aware DTSTART is the normal case;
    a rule whose UNTIL is naive is retried without the zone, which is the only
    shape that makes that comparison legal. Returns ``(rule, naive)`` or
    ``(None, False)`` when the rule cannot be parsed at all.
    """
    try:
        return rrulestr(rrule, dtstart=local_start), False
    except TypeError:
        pass
    except ValueError as error:
        logger.warning("calendar rrule is unparseable: %r (%s)", rrule, error)
        return None, False
    try:
        return rrulestr(rrule, dtstart=local_start.replace(tzinfo=None)), True
    except (ValueError, TypeError) as error:
        logger.warning("calendar rrule is unparseable: %r (%s)", rrule, error)
        return None, False


def _parse_exdates(raw: str, tz: ZoneInfo) -> set[datetime]:
    """EXDATE instants as aware UTC, ignoring anything unparseable."""
    exclusions: set[datetime] = set()
    for part in (raw or "").split(","):
        value = part.strip()
        if not value or "=" in value:
            # A property parameter (TZID=...) is not a value; Android stores
            # plain instants, so anything else is skipped rather than guessed.
            continue
        try:
            if value.endswith("Z"):
                moment = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=UTC
                )
            elif "T" in value:
                moment = datetime.strptime(value, "%Y%m%dT%H%M%S").replace(
                    tzinfo=tz
                )
            else:
                moment = datetime.strptime(value, "%Y%m%d").replace(tzinfo=tz)
        except ValueError:
            logger.warning("calendar exdate is unparseable, ignoring it: %r", part)
            continue
        exclusions.add(moment.astimezone(UTC))
    return exclusions


def _bound(moment: datetime, tz: ZoneInfo, naive: bool) -> datetime:
    local = _local(moment, tz)
    return local.replace(tzinfo=None) if naive else local


def _inside(
    moment: datetime | None,
    since: datetime | None,
    until: datetime | None,
) -> bool:
    if moment is None:
        return False
    if since is not None and moment < since:
        return False
    if until is not None and moment > until:
        return False
    return True


def _local(moment: datetime, tz: ZoneInfo) -> datetime:
    """Treat a stored naive-UTC instant as the same instant in ``tz``."""
    return moment.replace(tzinfo=UTC).astimezone(tz)


def _utc(moment: datetime) -> datetime:
    """Back to the naive-UTC form the rows use."""
    return moment.astimezone(UTC).replace(tzinfo=None)

def last_occurrence(
    *,
    starts_at: datetime | None,
    timezone: str,
    rrule: str,
    limit: int = 100_000,
) -> datetime | None:
    """When a bounded rule's final occurrence starts, as naive UTC.

    ``None`` means the rule has no ``UNTIL`` and no ``COUNT``, so it never ends;
    retention must keep such a row. A rule that cannot be parsed is treated the
    same way: an unreadable series is not evidence that it is over.

    Only ``UNTIL``/``COUNT`` make a rule finite, and those are checked on the
    text first, because walking an unbounded rule to see where it stops would
    never return.
    """
    if starts_at is None or not rrule.strip():
        return None
    upper = rrule.upper()
    if "UNTIL=" not in upper and "COUNT=" not in upper:
        return None
    tz = zone(timezone)
    rule, naive = _parse_rule(rrule, _local(starts_at, tz))
    if rule is None:
        return None
    last: datetime | None = None
    for index, occurrence in enumerate(rule):
        if index >= limit:
            return None
        last = occurrence
    if last is None:
        return None
    aware = last if last.tzinfo is not None else last.replace(tzinfo=tz)
    return _utc(aware)
