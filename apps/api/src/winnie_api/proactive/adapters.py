"""Adapters that connect the proactive engine to the host's capabilities.

The engine knows four small protocols; this module is where the phone service,
the weekly schedule, the push service and the channel configuration meet them.
It is the only place that imports both sides, which keeps `winnie-proactive`
free of any sibling capability.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

import httpx

from winnie_proactive.channels import EmailChannel, NtfyChannel
from winnie_proactive.ports import Channel, Message

OwnerResolver = Callable[[], Awaitable[str]]


class PushChannel:
    """The self-hosted app push, through the existing push capability."""

    name = "push"

    def __init__(self, service, *, resolve_owner: OwnerResolver) -> None:
        self.service = service
        self.resolve_owner = resolve_owner

    async def send(self, message: Message) -> str:
        owner = await self.resolve_owner()
        await self.service.notify(
            owner,
            title=message.title,
            body=message.body,
            data=dict(message.data),
        )
        return "push"


class PhoneCalendarAdapter:
    """Calendar occurrences, already expanded by the phone capability."""

    def __init__(self, service, *, resolve_owner: OwnerResolver) -> None:
        self.service = service
        self.resolve_owner = resolve_owner

    async def upcoming(self, *, since, until) -> list[dict[str, Any]]:
        owner = await self.resolve_owner()
        return await self.service.calendar(
            owner, since=since, until=until, limit=500
        )


class PhoneContactsAdapter:
    def __init__(self, service, *, resolve_owner: OwnerResolver) -> None:
        self.service = service
        self.resolve_owner = resolve_owner

    async def all(self) -> list[dict[str, Any]]:
        owner = await self.resolve_owner()
        return await self.service.contacts(owner, limit=2000)


class ScheduleAdapter:
    """The owner's weekly Markdown schedule, flattened into dated events.

    A read walks the weeks that overlap the range rather than every day: one
    parse per week, and a missing week file simply contributes nothing.
    """

    def __init__(self, service) -> None:
        self.service = service

    async def events(self, *, since: date, until: date) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        day = since
        seen: set[str] = set()
        while day <= until:
            try:
                week = await asyncio.to_thread(self.service.week, day)
            except Exception:  # noqa: BLE001 - a missing week is not an error
                day += timedelta(days=7)
                continue
            for section in week.get("days") or []:
                try:
                    on = date.fromisoformat(str(section.get("date")))
                except ValueError:
                    continue
                if not (since <= on <= until):
                    continue
                for event in section.get("events") or []:
                    key = f"{on.isoformat()}:{event.get('id')}"
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "id": str(event.get("id") or key),
                            "date": on.isoformat(),
                            "start": str(event.get("start") or ""),
                            "end": str(event.get("end") or ""),
                            "text": str(event.get("text") or ""),
                            "done": bool(event.get("done")),
                            "scope": str(event.get("scope") or "day"),
                        }
                    )
            day += timedelta(days=7)
        return rows


def build_channels(
    config,
    *,
    http: httpx.AsyncClient,
    push_service,
    resolve_owner: OwnerResolver,
) -> dict[str, Channel]:
    """Only channels that can actually deliver are registered.

    A rung that is configured but unreachable makes the ladder look longer than
    it is; an absent one is skipped at send time.
    """
    channels: dict[str, Channel] = {}
    if push_service is not None:
        channels["push"] = PushChannel(push_service, resolve_owner=resolve_owner)
    ntfy = config.channels.ntfy
    if ntfy.enabled and ntfy.topic:
        channels["ntfy"] = NtfyChannel(ntfy, http=http)
    email = config.channels.email
    if email.enabled and email.to and email.smtp_host:
        channels["email"] = EmailChannel(email)
    return channels
