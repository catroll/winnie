"""When something may interrupt, and when it waits.

This module is the answer to "how do you stop it being annoying". It never
looks at the model: the decision is arithmetic over the item's priority, the
local clock, the quiet window, the day's remaining budget and the digest slots.
The composer chooses words; the policy chooses silence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
from typing import Literal

DecisionAction = Literal["send", "defer", "digest"]


@dataclass(frozen=True)
class Decision:
    action: DecisionAction
    until: datetime | None = None
    reason: str = ""


def parse_hhmm(value: str) -> time:
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute or 0))


def in_quiet(local: datetime, window: tuple[str, str]) -> bool:
    start, end = parse_hhmm(window[0]), parse_hhmm(window[1])
    now = local.time()
    if start <= end:
        return start <= now < end
    # The window crosses midnight.
    return now >= start or now < end


def quiet_end(local: datetime, window: tuple[str, str]) -> datetime:
    """The next moment the quiet window ends, as an aware local datetime."""
    end = parse_hhmm(window[1])
    candidate = local.replace(
        hour=end.hour, minute=end.minute, second=0, microsecond=0
    )
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


def next_digest_slot(local: datetime, morning: str, evening: str) -> datetime:
    """The first digest slot after ``local`` (today or tomorrow morning)."""
    for value in (morning, evening):
        hour, _, minute = value.partition(":")
        candidate = local.replace(
            hour=int(hour), minute=int(minute or 0), second=0, microsecond=0
        )
        if candidate > local:
            return candidate
    tomorrow = (local + timedelta(days=1)).replace(
        hour=int(morning.split(":")[0]),
        minute=int(morning.split(":")[1] if ":" in morning else 0),
        second=0,
        microsecond=0,
    )
    return tomorrow


def decide(
    *,
    priority: int,
    due_at: datetime | None,
    now_local: datetime,
    timezone: tzinfo,
    quiet_hours: tuple[str, str],
    allow_urgent: bool,
    max_interrupts: int,
    sent_today: int,
    digest_morning: str,
    digest_evening: str,
) -> Decision:
    """Send now, hold until quiet hours end, or fold into the digest."""
    if priority >= 2:
        return Decision("digest", reason="digest priority")

    if in_quiet(now_local, quiet_hours):
        urgent = (
            priority == 0
            and allow_urgent
            and due_at is not None
            and _hours_away(due_at, timezone, now_local) <= 2
        )
        if not urgent:
            return Decision(
                "defer",
                until=quiet_end(now_local, quiet_hours),
                reason="quiet hours",
            )

    if priority == 1 and sent_today >= max_interrupts:
        return Decision(
            "digest",
            until=next_digest_slot(now_local, digest_morning, digest_evening),
            reason="daily interrupt budget spent",
        )

    return Decision("send")


def _hours_away(due_at: datetime, timezone: tzinfo, now_local: datetime) -> float:
    due = due_at.replace(tzinfo=timezone) if due_at.tzinfo is None else due_at
    return (due.astimezone(timezone) - now_local).total_seconds() / 3600.0


__all__ = [
    "Decision",
    "decide",
    "in_quiet",
    "next_digest_slot",
    "parse_hhmm",
    "quiet_end",
]
