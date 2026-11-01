"""Find the things worth saying, from data the server already holds.

Detection is ordinary code: rows in, findings out. It has no model call and no
opinion about whether to interrupt — that is :mod:`winnie_proactive.policy`.
Every finding keeps the evidence it came from, so a claimed date can be traced
back to a calendar row or a contact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

# A birthday calendar often stores the person's name as the title, so the
# calendar name carries the signal ("生日"/"Birthdays"). The assistant's
# `birthdays` tool keeps the same two checks on purpose: two surfaces, one rule.
BIRTHDAY_WORDS = re.compile(
    r"生日|birthday|诞辰|誕辰|纪念日|anniversary|壽|寿", re.IGNORECASE
)
TITLE_NOISE = re.compile(
    r"(的)?生日|birthday|诞辰|誕辰|纪念日|anniversary"
    r"|[0-9一二两三四五六七八九十百廿]+岁|\U0001F382|农历|農曆",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Birthday:
    """One person, one date, and where it came from."""

    name: str
    on: date
    note: str
    evidence: dict[str, Any]
    year: int | None = None


@dataclass(frozen=True)
class WorkEvent:
    id: str
    on: date
    start: str
    text: str


@dataclass(frozen=True)
class Findings:
    birthdays: list[Birthday] = field(default_factory=list)
    leave_by: list[WorkEvent] = field(default_factory=list)
    overdue: list[WorkEvent] = field(default_factory=list)


def matches_birthday(title: str, calendar: str) -> bool:
    return bool(BIRTHDAY_WORDS.search(title) or BIRTHDAY_WORDS.search(calendar))


def person_from_title(title: str) -> str:
    name = TITLE_NOISE.sub("", title)
    name = re.sub(r"[（(][^()（）]*[)）]", "", name)
    name = name.strip(" ·-|,，。:：[]【】")
    return " ".join(name.split()) or title.strip() or "?"


def next_occurrence(today: date, month: int, day: int) -> date | None:
    """The next occurrence on or after ``today``; Feb 29 falls back to Feb 28."""
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            if month == 2 and day == 29:
                candidate = date(year, 2, 28)
            else:
                return None
        if candidate >= today:
            return candidate
    return None


async def birthday_findings(
    calendar: Any,
    contacts: Any,
    *,
    today: date,
    zone,
) -> list[Birthday]:
    """Birthdays from the calendar and the contacts, in ``[today, today+365]``.

    The calendar is the only source with lunar dates: its provider wrote the
    lunar month/day into the rule and the read already resolved the occurrence.
    Contacts carry the ones a phone never put on a calendar.
    """
    found: list[Birthday] = []
    # A full year from today, not the calendar year: December must still see
    # January, and a Feb 29 "today" has no Feb 29 one year later.
    try:
        until_date = today.replace(year=today.year + 1)
    except ValueError:
        until_date = today.replace(year=today.year + 1, day=28)
    since = datetime.combine(today, datetime.min.time(), tzinfo=zone)
    until = datetime.combine(until_date, datetime.max.time(), tzinfo=zone)
    for row in await calendar.upcoming(since=since, until=until):
        title = str(row.get("title") or "")
        calendar_name = str(row.get("calendar_name") or "")
        if not matches_birthday(title, calendar_name):
            continue
        moment = _as_datetime(row.get("starts_at"), zone)
        if moment is None:
            continue
        on = moment.astimezone(zone).date()
        if not (today <= on <= until.date()):
            continue
        recurring = bool(row.get("recurrence"))
        found.append(
            Birthday(
                name=person_from_title(title),
                on=on,
                note="（每年）" if recurring else "（一次性）",
                evidence={
                    "source": "calendar",
                    "id": row.get("id") or "",
                    "title": title,
                    "starts_at": moment.isoformat(),
                },
            )
        )
    for contact in await contacts.all():
        name = str(contact.get("display_name") or "").strip()
        for item in contact.get("birthdays") or []:
            try:
                month = int(item.get("month") or 0)
                day = int(item.get("day") or 0)
            except (TypeError, ValueError):
                continue
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            on = next_occurrence(today, month, day)
            if on is None:
                continue
            note = "（通讯录）"
            if item.get("type") == "anniversary":
                note = "（纪念日）"
            year = item.get("year")
            found.append(
                Birthday(
                    name=name or "?",
                    on=on,
                    note=note,
                    year=int(year) if year else None,
                    evidence={
                        "source": "contacts",
                        "id": contact.get("id") or "",
                        "name": name,
                    },
                )
            )
    return found


async def work_findings(
    schedule: Any,
    *,
    today: date,
    leave_buffer_minutes: int,
    overdue_days: int,
) -> tuple[list[WorkEvent], list[WorkEvent]]:
    """Timed items to leave for, and unchecked items from earlier days."""
    since = today - timedelta(days=overdue_days)
    rows = await schedule.events(since=since, until=today)
    leave: list[WorkEvent] = []
    overdue: list[WorkEvent] = []
    for row in rows:
        raw = str(row.get("date") or "")
        try:
            on = date.fromisoformat(raw)
        except ValueError:
            continue
        start = str(row.get("start") or "")
        text = str(row.get("text") or "").strip() or "(untitled)"
        done = bool(row.get("done"))
        if on == today and start and not done:
            leave.append(WorkEvent(id=str(row.get("id") or text), on=on, start=start, text=text))
        if on < today and not done and row.get("scope") == "day":
            overdue.append(WorkEvent(id=str(row.get("id") or text), on=on, start=start, text=text))
    leave.sort(key=lambda item: item.start)
    overdue.sort(key=lambda item: (item.on, item.text))
    return leave, overdue


def _as_datetime(value: Any, zone) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    return moment


__all__ = [
    "Birthday",
    "Findings",
    "WorkEvent",
    "birthday_findings",
    "matches_birthday",
    "next_occurrence",
    "person_from_title",
    "work_findings",
]
