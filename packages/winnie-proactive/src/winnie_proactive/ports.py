"""The small boundary between the engine and whoever owns the data.

The capability imports no sibling capability: the host adapts the phone
service, the schedule tree, the push service and the model to these protocols.
Without an adapter the corresponding detector or channel is simply absent, so
a deployment pays nothing for a feature it does not run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol


class CalendarPort(Protocol):
    """Calendar occurrences in a window, already expanded by the owner."""

    async def upcoming(
        self, *, since: datetime, until: datetime
    ) -> list[dict[str, Any]]: ...


class ContactsPort(Protocol):
    """Every synced contact, with its ``birthdays`` list."""

    async def all(self) -> list[dict[str, Any]]: ...


class SchedulePort(Protocol):
    """The owner's weekly Markdown schedule, as normalised events.

    ``events`` returns dicts with ``id``, ``date`` (ISO or None), ``start``
    (``HH:MM`` or ""), ``end``, ``text``, ``done`` and ``scope``.
    """

    async def events(
        self, *, since: date, until: date
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Message:
    """What a channel is asked to deliver."""

    title: str
    body: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class Channel(Protocol):
    """One delivery path. ``send`` returns a receipt or raises ChannelError."""

    name: str

    async def send(self, message: Message) -> str: ...


class ChannelError(RuntimeError):
    """The channel could not deliver; the ladder moves on."""


__all__ = [
    "CalendarPort",
    "Channel",
    "ChannelError",
    "ContactsPort",
    "Message",
    "SchedulePort",
]
