"""Assistant tools over the owner's phone data.

The tools follow two rules that the model cannot be trusted to infer:

1. A phone is a *remote* writer. Every write reaches it as a command that it runs
   on its next sync. Creating an event is submitted without asking (see
   ``UNATTENDED_WRITES``); every other write is a proposal until the owner
   confirms it in chat. Either way a tool never claims the phone has run it.
2. Synced data is a snapshot, not the live phone. Every answer carries the last
   sync time so the model can say how fresh it is.

Formatting lives here, next to the tools, so the assistant prompt and the tool
output use one time zone and one date format.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from winnie_assistant.phone import PhoneGateway

logger = logging.getLogger(__name__)


class PhoneToolError(RuntimeError):
    """A phone tool call that cannot be completed for a reportable reason."""


# Writes the assistant submits without asking first.
#
# The owner asked for this on 2026-11-03: an event the assistant got slightly
# wrong costs one message to correct, while a "确认" round trip costs a turn on
# every correct one, and the confirmation was being spent on a write that a
# second message can undo. Creation only — update, delete and sms.send still
# wait, because those act on something the owner did not just ask for.
UNATTENDED_WRITES = frozenset({"calendar.create"})


PHONE_KINDS = (
    "calendar",
    "call",
    "message",
    "contact",
    "notification",
    "location",
    "app",
)

PHONE_READ_TOOLS = frozenset(
    {
        "phone_status",
        "phone_search",
        "phone_calendar",
        "birthdays",
        "phone_calls",
        "phone_messages",
        "phone_notifications",
        "phone_location",
        "phone_apps",
    }
)
PHONE_WRITE_TOOLS = frozenset(
    {
        "phone_calendar_create",
        "phone_calendar_update",
        "phone_calendar_delete",
        "phone_sms_send",
        "phone_confirm",
        "phone_cancel",
    }
)
PHONE_TOOLS = PHONE_READ_TOOLS | PHONE_WRITE_TOOLS


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


PHONE_TOOL_SCHEMAS: list[dict[str, Any]] = [
    _schema(
        "phone_status",
        (
            "Report which phone synced, how fresh its data is, what it syncs and "
            "how many records exist of each kind. Call it first when the owner "
            "asks something about their phone and you are unsure whether data "
            "has synced, or when a phone tool returns nothing."
        ),
        {},
        [],
    ),
    _schema(
        "phone_search",
        (
            "Search every kind of synced phone data at once: calendar, call "
            "log, SMS, contacts, notifications, location and apps. Use it when "
            "the owner asks whether they were contacted, what was said, or when "
            "something happened. Returns the record kind, time, title and text."
        ),
        {
            "query": {
                "type": "string",
                "description": "Words to match in names, numbers, titles or message text.",
            },
            "kinds": {
                "type": "string",
                "description": (
                    "Comma-separated subset of "
                    "calendar,call,message,contact,notification,location,app. "
                    "Empty searches all."
                ),
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3650,
                "description": "How far back to search; default 30.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum hits; default 10.",
            },
        },
        ["query"],
    ),
    _schema(
        "phone_calendar",
        (
            "Read calendar events from the phone for a window around now. Each "
            "line carries the event id needed by phone_calendar_update and "
            "phone_calendar_delete."
        ),
        {
            "days_back": {
                "type": "integer",
                "minimum": 0,
                "maximum": 365,
                "description": "Days before now to include; default 0.",
            },
            "days_ahead": {
                "type": "integer",
                "minimum": 0,
                "maximum": 365,
                "description": "Days after now to include; default 7.",
            },
            "query": {
                "type": "string",
                "description": "Optional title, description or location filter.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum events; default 50.",
            },
        },
        [],
    ),
    _schema(
        "birthdays",
        (
            "List the birthdays and anniversaries coming up in the next N days, "
            "merged from the phone calendar and the contacts. Use this for any "
            "'who has a birthday soon' question; never answer it from memory. The "
            "calendar tool's default window is only seven days, so it cannot "
            "answer this on its own."
        ),
        {
            "days_ahead": {
                "type": "integer",
                "minimum": 1,
                "maximum": 366,
                "description": "Days to look ahead; default 60.",
            },
        },
        [],
    ),
    _schema(
        "phone_calls",
        (
            "Read the phone's call log, newest first. Includes missed, rejected "
            "and outgoing calls with duration."
        ),
        {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3650,
                "description": "How far back to look; default 7.",
            },
            "direction": {
                "type": "string",
                "description": (
                    "incoming, outgoing, missed, rejected, blocked or "
                    "voicemail. Empty means all."
                ),
            },
            "query": {
                "type": "string",
                "description": "Optional number or contact name filter.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum entries; default 20.",
            },
        },
        [],
    ),
    _schema(
        "phone_messages",
        (
            "Read SMS from the phone, newest first, with unread state. Text "
            "only: MMS attachments are not synced."
        ),
        {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3650,
                "description": "How far back to look; default 7.",
            },
            "address": {
                "type": "string",
                "description": "Optional phone number or partial number.",
            },
            "query": {
                "type": "string",
                "description": "Optional words to match in the message body or address.",
            },
            "unread_only": {
                "type": "boolean",
                "description": "Only unread messages; default false.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum messages; default 20.",
            },
        },
        [],
    ),
    _schema(
        "phone_notifications",
        (
            "Read notifications the phone captured, newest first. Useful for "
            "what apps told the owner recently; these are not messages from the "
            "owner and may contain verification codes."
        ),
        {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 90,
                "description": "How far back to look; default 1.",
            },
            "app": {
                "type": "string",
                "description": "Optional app label filter, for example WeChat.",
            },
            "query": {
                "type": "string",
                "description": "Optional words to match in the title or text.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum notifications; default 20.",
            },
        },
        [],
    ),
    _schema(
        "phone_location",
        (
            "Read recent location fixes recorded at phone syncs, newest first. "
            "Each fix is a point in time, not a continuous track."
        ),
        {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 90,
                "description": "How far back to look; default 1.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Maximum fixes; default 20.",
            },
        },
        [],
    ),
    _schema(
        "phone_apps",
        (
            "List installed applications on the phone, with version and package "
            "name. Use it when the owner asks what is installed or whether an "
            "app is present."
        ),
        {
            "query": {
                "type": "string",
                "description": "Optional app label or package name filter.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Maximum apps; default 50.",
            },
        },
        [],
    ),
    _schema(
        "phone_calendar_create",
        (
            "CREATE a calendar event on the phone. It is submitted immediately, "
            "without asking the owner first. Use an ISO-8601 start time with an "
            "offset, for example 2026-11-02T15:00:00+08:00, then tell the owner "
            "exactly what was submitted: title, local date and time range, place. "
            "They correct it afterwards if it is wrong, so do not ask them to "
            "confirm before calling this."
        ),
        {
            "title": {"type": "string", "description": "Event title."},
            "starts_at": {
                "type": "string",
                "description": "ISO-8601 start time with an offset.",
            },
            "ends_at": {
                "type": "string",
                "description": "ISO-8601 end time; one hour after the start by default.",
            },
            "location": {"type": "string", "description": "Optional place."},
            "description": {"type": "string", "description": "Optional notes."},
            "calendar_name": {
                "type": "string",
                "description": "Optional calendar to write to; the primary one by default.",
            },
            "all_day": {
                "type": "boolean",
                "description": "All-day event; default false.",
            },
        },
        ["title", "starts_at"],
    ),
    _schema(
        "phone_calendar_update",
        (
            "PROPOSE changing an existing calendar event on the phone. Pass the "
            "event id from phone_calendar and only the fields to change. Nothing "
            "is written until the owner confirms."
        ),
        {
            "event_id": {
                "type": "string",
                "description": "Event id shown by phone_calendar.",
            },
            "title": {"type": "string"},
            "starts_at": {
                "type": "string",
                "description": "New ISO-8601 start time with an offset.",
            },
            "ends_at": {
                "type": "string",
                "description": "New ISO-8601 end time with an offset.",
            },
            "location": {"type": "string"},
            "description": {"type": "string"},
        },
        ["event_id"],
    ),
    _schema(
        "phone_calendar_delete",
        (
            "PROPOSE deleting a calendar event on the phone. Pass the event id "
            "from phone_calendar. Nothing is deleted until the owner confirms."
        ),
        {
            "event_id": {
                "type": "string",
                "description": "Event id shown by phone_calendar.",
            }
        },
        ["event_id"],
    ),
    _schema(
        "phone_sms_send",
        (
            "PROPOSE sending an SMS from the phone. Nothing is sent yet. Read "
            "the recipient and the exact text back to the owner and wait for an "
            "explicit yes, then call phone_confirm. Keep the message short and "
            "never invent a verification code."
        ),
        {
            "address": {
                "type": "string",
                "description": "Recipient phone number in international or local form.",
            },
            "body": {
                "type": "string",
                "description": "Exact message text, at most 2000 characters.",
            },
        },
        ["address", "body"],
    ),
    _schema(
        "phone_confirm",
        (
            "Confirm a pending phone write. Use only when the owner's current "
            "message is an explicit yes to the exact proposal. The phone runs it "
            "on its next sync."
        ),
        {
            "command_id": {
                "type": "string",
                "description": "The id from the PENDING message.",
            }
        },
        ["command_id"],
    ),
    _schema(
        "phone_cancel",
        (
            "Cancel a pending phone write. Use it when the owner declines, "
            "changes their mind or wants to stop a command that has not run yet."
        ),
        {
            "command_id": {
                "type": "string",
                "description": "The id from the PENDING message.",
            }
        },
        ["command_id"],
    ),
]


class PhoneToolMixin:
    """Phone tool implementations, mixed into ``ToolBox``.

    The attributes are declared here so the mixin can be read on its own; the
    toolbox sets them in ``__init__``.
    """

    phone: PhoneGateway | None = None
    timezone: ZoneInfo = ZoneInfo("UTC")
    clock: Callable[[], datetime] | None = None

    @property
    def has_phone(self) -> bool:
        return self.phone is not None

    # -- dispatch --------------------------------------------------------

    async def _phone_tool(self, name: str, arguments: dict[str, Any]) -> str:
        gateway = self.phone
        if gateway is None:
            raise PhoneToolError("phone data is disabled on the server")
        if name == "phone_status":
            return await self._phone_status(gateway)
        if name == "phone_search":
            return await self._phone_search(gateway, arguments)
        if name == "phone_calendar":
            return await self._phone_calendar(gateway, arguments)
        if name == "birthdays":
            return await self._birthdays(gateway, arguments)
        if name == "phone_calls":
            return await self._phone_calls(gateway, arguments)
        if name == "phone_messages":
            return await self._phone_messages(gateway, arguments)
        if name == "phone_notifications":
            return await self._phone_notifications(gateway, arguments)
        if name == "phone_location":
            return await self._phone_locations(gateway, arguments)
        if name == "phone_apps":
            return await self._phone_apps(gateway, arguments)
        if name == "phone_calendar_create":
            return await self._phone_calendar_create(gateway, arguments)
        if name == "phone_calendar_update":
            return await self._phone_calendar_update(gateway, arguments)
        if name == "phone_calendar_delete":
            return await self._phone_propose(
                gateway, "calendar.delete", {"event_id": _required(arguments, "event_id")}
            )
        if name == "phone_sms_send":
            return await self._phone_propose(
                gateway,
                "sms.send",
                {
                    "address": _required(arguments, "address"),
                    "body": _required(arguments, "body"),
                },
            )
        if name == "phone_confirm":
            return await self._phone_confirm(gateway, arguments)
        if name == "phone_cancel":
            return await self._phone_cancel(gateway, arguments)
        raise PhoneToolError(f"unknown phone tool: {name}")

    # -- reads -----------------------------------------------------------

    async def _phone_status(self, gateway: PhoneGateway) -> str:
        summary = await gateway.summary()
        devices = summary.get("devices") or []
        if not devices:
            return (
                "No phone has synced yet. Install the Winnie Android app, sign in, "
                "open Phone data, grant the scopes to share and run a sync."
            )
        lines: list[str] = []
        for entry in devices:
            device = entry.get("device") or {}
            counts = {row.get("kind"): row.get("count") for row in entry.get("counts") or []}
            lines.append(
                f"device: {device.get('name') or device.get('model') or 'phone'}"
                f" ({device.get('model') or 'unknown model'}, Android"
                f" {device.get('os_version') or '?'}, app {device.get('app_version') or '?'})"
            )
            lines.append(
                "last sync: "
                + (self._fmt(device.get("last_sync_at")) or "never")
                + ("" if device.get("revoked") else "")
            )
            scopes = ", ".join(device.get("scopes") or []) or "none"
            lines.append(f"scopes: {scopes}")
            lines.append(
                "records: "
                + ", ".join(
                    f"{kind}={counts.get(kind, 0)}"
                    for kind in PHONE_KINDS
                    if counts.get(kind) is not None
                )
            )
        last_sync = summary.get("last_sync_at")
        stale = summary.get("stale_minutes")
        if last_sync is not None:
            lines.append(
                "freshest sync: "
                + self._fmt(last_sync)
                + (f" ({stale} min ago)" if stale is not None else "")
            )
        pending = int(summary.get("pending_commands") or 0)
        if pending:
            lines.append(f"pending phone commands: {pending}")
        return "\n".join(lines)

    async def _phone_search(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise PhoneToolError("phone_search requires a non-empty query")
        raw_kinds = str(arguments.get("kinds") or "").strip()
        kinds = [kind.strip() for kind in raw_kinds.split(",") if kind.strip()]
        unknown = [kind for kind in kinds if kind not in PHONE_KINDS]
        if unknown:
            raise PhoneToolError(f"unknown kinds: {', '.join(unknown)}")
        days = _as_int(arguments.get("days"), default=30, minimum=1, maximum=3650)
        limit = _as_int(arguments.get("limit"), default=10, minimum=1, maximum=50)
        hits = await gateway.search(
            query,
            kinds=kinds or None,
            since=self._now() - timedelta(days=days),
            limit=limit,
        )
        if not hits:
            return f"No phone record matched {query!r} in the last {days} days."
        lines = []
        for hit in hits:
            when = self._fmt(hit.get("at"))
            title = hit.get("title") or ""
            text = _clip(str(hit.get("text") or ""), 300)
            lines.append(
                f"- [{hit.get('kind')}]"
                + (f" {when}" if when else "")
                + f" {title}"
                + (f": {text}" if text else "")
                + f" ({hit.get('device_name') or 'phone'})"
            )
        return "\n".join(lines)

    async def _phone_calendar(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        days_back = _as_int(arguments.get("days_back"), default=0, minimum=0, maximum=365)
        days_ahead = _as_int(
            arguments.get("days_ahead"), default=7, minimum=0, maximum=365
        )
        limit = _as_int(arguments.get("limit"), default=50, minimum=1, maximum=100)
        query = str(arguments.get("query") or "").strip()
        now = self._now()
        events = await gateway.calendar(
            since=now - timedelta(days=days_back),
            until=now + timedelta(days=days_ahead),
            query=query,
            limit=limit,
        )
        if not events:
            return "No calendar event in that window."
        lines = []
        for event in events:
            start = self._fmt(event.get("starts_at"))
            end = self._fmt(event.get("ends_at"))
            end_time = end.split(" ")[-1] if end else ""
            when = f"{start}-{end_time}" if end_time else start
            if event.get("all_day"):
                when = f"{start[:10]} (all day)"
            location = f" @ {event['location']}" if event.get("location") else ""
            calendar = (
                f" [{event['calendar_name']}]" if event.get("calendar_name") else ""
            )
            # An expanded occurrence carries its series id and rule, so say so:
            # several lines with the same id are one repeating event, and an
            # update or delete of that id touches the series.
            repeating = " (recurring)" if event.get("recurrence") else ""
            lines.append(
                f"- {when} [id {event['id']}] {event.get('title') or '(untitled)'}"
                f"{location}{calendar}{repeating}"
            )
        return "\n".join(lines)

    async def _birthdays(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        """Birthdays and anniversaries ahead, merged from two sources.

        The calendar carries the lunar ones (the provider writes the lunar date
        into the rule and the read resolves it) and contacts carry the ones a
        phone never put on a calendar. Neither alone is the answer: the owner's
        data has both, and one person can appear in each with a different date.
        """
        days = _as_int(arguments.get("days_ahead"), default=60, minimum=1, maximum=366)
        now = self._now()
        today = now.astimezone(self.timezone).date()
        horizon = today + timedelta(days=days)
        events = await gateway.calendar(
            since=now, until=now + timedelta(days=days), limit=200
        )
        contacts = await gateway.contacts(limit=2000)

        found: list[_Birthday] = []
        for event in events:
            title = str(event.get("title") or "")
            calendar = str(event.get("calendar_name") or "")
            if not _birthday_text_matches(title, calendar):
                continue
            when = _parse_datetime(event.get("starts_at"), self.timezone)
            if when is None:
                continue
            on = when.astimezone(self.timezone).date()
            if not (today <= on <= horizon):
                continue
            recurring = bool(event.get("recurrence"))
            found.append(
                _Birthday(
                    name=_person_from_title(title),
                    on=on,
                    source=f"calendar「{title}」",
                    note="every year" if recurring else "one-off",
                )
            )
        for contact in contacts:
            name = str(contact.get("display_name") or "").strip()
            for item in contact.get("birthdays") or []:
                try:
                    month = int(item.get("month") or 0)
                    day = int(item.get("day") or 0)
                except (TypeError, ValueError):
                    continue
                if not (1 <= month <= 12 and 1 <= day <= 31):
                    continue
                on = _next_date(today, month, day)
                if on is None or on > horizon:
                    continue
                kind = "anniversary" if item.get("type") == "anniversary" else "birthday"
                note = f"contacts, {kind}"
                year = item.get("year")
                if year:
                    note += f", turns {on.year - int(year)}"
                found.append(
                    _Birthday(name=name or "?", on=on, source="contacts", note=note)
                )
        if not found:
            return (
                f"No birthday or anniversary in the next {days} days. If the owner "
                "expects one, check that the contact and calendar scopes are enabled "
                "and synced (phone_status)."
            )

        found.sort(key=lambda item: (item.on, item.name))
        lines = [f"Birthdays and anniversaries in the next {days} days:"]
        for item in found:
            left = (item.on - today).days
            when = "today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days"
            lines.append(
                f"- {item.on.isoformat()} {_WEEKDAY[item.on.weekday()]} \u00b7 {item.name}"
                f" \u00b7 {when} \u00b7 {item.source} \u00b7 {item.note}"
            )
        dates_by_name: dict[str, set[date]] = {}
        for item in found:
            dates_by_name.setdefault(item.name, set()).add(item.on)
        for name, dates in dates_by_name.items():
            if len(dates) < 2:
                continue
            listed = ", ".join(day.isoformat() for day in sorted(dates))
            lines.append(
                f"Note: {name} appears with {len(dates)} different dates ({listed})."
                " Ask the owner which one is right before recording it."
            )
        return "\n".join(lines)

    async def _phone_calls(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        days = _as_int(arguments.get("days"), default=7, minimum=1, maximum=3650)
        limit = _as_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
        direction = str(arguments.get("direction") or "").strip()
        query = str(arguments.get("query") or "").strip()
        calls = await gateway.calls(
            since=self._now() - timedelta(days=days),
            direction=direction,
            query=query,
            limit=limit,
        )
        if not calls:
            return f"No call in the last {days} days."
        lines = []
        for call in calls:
            who = call.get("cached_name") or call.get("number") or "unknown number"
            duration = int(call.get("duration_seconds") or 0)
            length = f" {duration // 60}m{duration % 60:02d}s" if duration else ""
            lines.append(
                f"- {self._fmt(call.get('started_at'))} {call.get('direction')}"
                f" {who}{length}"
            )
        return "\n".join(lines)

    async def _phone_messages(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        days = _as_int(arguments.get("days"), default=7, minimum=1, maximum=3650)
        limit = _as_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
        messages = await gateway.messages(
            since=self._now() - timedelta(days=days),
            address=str(arguments.get("address") or "").strip(),
            query=str(arguments.get("query") or "").strip(),
            unread_only=bool(arguments.get("unread_only")),
            limit=limit,
        )
        if not messages:
            return f"No SMS in the last {days} days."
        lines = []
        for message in messages:
            flag = "" if message.get("is_read") else " [unread]"
            body = _clip(str(message.get("body") or "").replace("\n", " "), 300)
            lines.append(
                f"- {self._fmt(message.get('received_at'))}"
                f" {message.get('direction')} {message.get('address') or 'unknown'}"
                f"{flag}: {body}"
            )
        return "\n".join(lines)

    async def _phone_notifications(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        days = _as_int(arguments.get("days"), default=1, minimum=1, maximum=90)
        limit = _as_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
        notifications = await gateway.notifications(
            since=self._now() - timedelta(days=days),
            app=str(arguments.get("app") or "").strip(),
            query=str(arguments.get("query") or "").strip(),
            limit=limit,
        )
        if not notifications:
            return f"No notification in the last {days} days."
        lines = []
        for item in notifications:
            text = _clip(str(item.get("text") or "").replace("\n", " "), 200)
            title = str(item.get("title") or "")
            lines.append(
                f"- {self._fmt(item.get('posted_at'))}"
                f" {item.get('app_label') or item.get('package_name') or 'app'}"
                + (f": {title}" if title else "")
                + (f" — {text}" if text else "")
            )
        return "\n".join(lines)

    async def _phone_locations(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        days = _as_int(arguments.get("days"), default=1, minimum=1, maximum=90)
        limit = _as_int(arguments.get("limit"), default=20, minimum=1, maximum=500)
        fixes = await gateway.locations(
            since=self._now() - timedelta(days=days), limit=limit
        )
        if not fixes:
            return "No location fix has been recorded."
        lines = []
        for fix in fixes:
            accuracy = fix.get("accuracy_m")
            # The address is what makes a fix answerable ("where was I"); the
            # coordinates stay next to it so nothing is lost without a provider.
            place = str(fix.get("address") or "").strip()
            coordinates = (
                f"{float(fix.get('latitude') or 0):.5f},"
                f"{float(fix.get('longitude') or 0):.5f}"
            )
            lines.append(
                f"- {self._fmt(fix.get('captured_at'))}"
                f" {place + ' ' if place else ''}({coordinates})"
                + (f" ±{float(accuracy):.0f}m" if accuracy else "")
                + (f" via {fix['provider']}" if fix.get("provider") else "")
            )
        return "\n".join(lines)

    async def _phone_apps(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        limit = _as_int(arguments.get("limit"), default=50, minimum=1, maximum=500)
        apps = await gateway.apps(
            query=str(arguments.get("query") or "").strip(), limit=limit
        )
        if not apps:
            return "No installed application matched."
        lines = []
        for app in apps:
            version = f" {app['version_name']}" if app.get("version_name") else ""
            system = " [system]" if app.get("is_system") else ""
            lines.append(
                f"- {app.get('label') or app.get('package_name')}"
                f" ({app.get('package_name')}){version}{system}"
            )
        return "\n".join(lines)

    # -- writes ----------------------------------------------------------

    async def _phone_calendar_create(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        starts_at = _parse_datetime(arguments.get("starts_at"), self.timezone)
        if starts_at is None:
            raise PhoneToolError("phone_calendar_create needs an ISO-8601 starts_at")
        payload: dict[str, Any] = {
            "title": _required(arguments, "title"),
            "starts_at": starts_at.isoformat(),
        }
        ends_at = _parse_datetime(arguments.get("ends_at"), self.timezone)
        if ends_at is not None:
            payload["ends_at"] = ends_at.isoformat()
        for field in ("location", "description", "calendar_name"):
            value = str(arguments.get(field) or "").strip()
            if value:
                payload[field] = value
        if arguments.get("all_day"):
            payload["all_day"] = True
        return await self._phone_propose(gateway, "calendar.create", payload)

    async def _phone_calendar_update(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        payload: dict[str, Any] = {"event_id": _required(arguments, "event_id")}
        for field in ("title", "location", "description"):
            if arguments.get(field) is not None:
                payload[field] = str(arguments.get(field) or "")
        for field in ("starts_at", "ends_at"):
            parsed = _parse_datetime(arguments.get(field), self.timezone)
            if parsed is not None:
                payload[field] = parsed.isoformat()
        if len(payload) == 1:
            raise PhoneToolError(
                "phone_calendar_update needs at least one field to change"
            )
        return await self._phone_propose(gateway, "calendar.update", payload)

    async def _phone_propose(
        self, gateway: PhoneGateway, kind: str, payload: dict[str, Any]
    ) -> str:
        command = await gateway.propose_command(kind, payload)
        if kind in UNATTENDED_WRITES:
            return await self._phone_submit(gateway, command, payload)
        command_id = command.get("id")
        summary = command.get("summary") or kind
        expires = self._fmt(command.get("expires_at"))
        return (
            f"PENDING {command_id}: {summary}.\n"
            "Nothing has been written yet. Ask the owner to confirm this exact "
            "action in their next message. Only after an explicit yes, call "
            "phone_confirm with this id. The phone executes it on its next sync,"
            " so it may take a few minutes"
            + (f"; the proposal expires at {expires}" if expires else "")
            + "."
        )

    async def _phone_submit(
        self,
        gateway: PhoneGateway,
        command: dict[str, Any],
        payload: dict[str, Any],
    ) -> str:
        """Approve a write the owner asked to have submitted, and report it.

        The proposal is still created and approved — the audit trail stays — but
        no turn is spent asking. What comes back has to be the submitted event
        in the owner's own terms (title, local time, place), because that text is
        what their next message corrects.
        """
        approved = await gateway.approve_command(str(command.get("id")))
        title = str(payload.get("title") or "").strip()
        starts = self._fmt(payload.get("starts_at"))
        ends = self._fmt(payload.get("ends_at"))
        if starts and ends:
            window = (
                f"{starts}–{ends[11:]}" if ends[:10] == starts[:10] else f"{starts}–{ends}"
            )
        else:
            window = starts
        lines = [f"SUBMITTED {approved.get('id')}: {title or approved.get('summary') or 'calendar event'}"]
        if window:
            lines.append(f"  time: {window} ({self.timezone.key})")
        if payload.get("location"):
            lines.append(f"  place: {payload['location']}")
        if payload.get("all_day"):
            lines.append("  all day")
        lines.append(
            "No confirmation was asked or needed. The phone writes it at its "
            "next sync, so it may take a few minutes. Tell the owner exactly this "
            "title, time and place; if any of it is wrong they will say so and "
            "you can update or delete it."
        )
        return "\n".join(lines)

    async def _phone_confirm(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        command_id = _required(arguments, "command_id")
        command = await gateway.approve_command(command_id)
        return (
            f"APPROVED {command.get('id')}: {command.get('summary') or command.get('kind')}."
            " The phone will run it at its next sync; the result appears on the"
            " phone, not here."
        )

    async def _phone_cancel(
        self, gateway: PhoneGateway, arguments: dict[str, Any]
    ) -> str:
        command_id = _required(arguments, "command_id")
        command = await gateway.cancel_command(command_id)
        return (
            f"CANCELLED {command.get('id')}: {command.get('summary') or command.get('kind')}."
            " Nothing will be written."
        )

    # -- prompt digest ---------------------------------------------------

    async def phone_digest(self, now: datetime) -> str:
        """A compact phone summary for the system prompt.

        Empty when no phone is configured or nothing has synced: an empty
        section is better than a prompt that says "unknown" five times.
        """
        if self.phone is None:
            return ""
        try:
            summary = await self.phone.summary()
            devices = summary.get("devices") or []
            if not devices:
                return ""
            device = devices[0].get("device") or {}
            local_now = now.astimezone(self.timezone)
            midnight = local_now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).astimezone(timezone.utc)
            events = await self.phone.calendar(
                since=now, until=now + timedelta(hours=24), limit=4
            )
            unread = await self.phone.messages(
                since=now - timedelta(days=3), unread_only=True, limit=3
            )
            missed = await self.phone.calls(
                since=midnight, direction="missed", limit=3
            )
        except Exception:  # noqa: BLE001 - a digest must never break a turn
            logger.warning("phone digest failed", exc_info=True)
            return ""
        lines = [
            f"Phone {device.get('name') or 'device'}; last sync "
            f"{self._fmt(device.get('last_sync_at')) or 'never'}"
            + (
                f" ({summary.get('stale_minutes')} min ago)"
                if summary.get("stale_minutes") is not None
                else ""
            )
            + "."
        ]
        if events:
            lines.append("Next 24h:")
            for event in events:
                start = self._fmt(event.get("starts_at"))
                lines.append(
                    f"  {start} {event.get('title') or '(untitled)'}"
                    + (f" @ {event['location']}" if event.get("location") else "")
                )
        if unread:
            lines.append("Unread SMS:")
            for message in unread:
                body = _clip(str(message.get("body") or "").replace("\n", " "), 80)
                lines.append(
                    f"  {self._fmt(message.get('received_at'))} "
                    f"{message.get('address')}: {body}"
                )
        if missed:
            lines.append("Missed calls today:")
            for call in missed:
                lines.append(
                    f"  {self._fmt(call.get('started_at'))} "
                    f"{call.get('cached_name') or call.get('number') or 'unknown'}"
                )
        return "\n".join(lines)[:1200]

    # -- helpers ---------------------------------------------------------

    def _now(self) -> datetime:
        clock = self.clock or (lambda: datetime.now(timezone.utc))
        value = clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _fmt(self, value: Any) -> str:
        parsed = _parse_datetime(value, self.timezone)
        if parsed is None:
            return ""
        return parsed.astimezone(self.timezone).strftime("%Y-%m-%d %H:%M")


_WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_BIRTHDAY_WORDS = re.compile(
    r"生日|birthday|诞辰|誕辰|纪念日|anniversary|壽|寿", re.IGNORECASE
)
_TITLE_NOISE = re.compile(
    r"(的)?生日|birthday|诞辰|誕辰|纪念日|anniversary"
    r"|[0-9一二两三四五六七八九十百廿]+岁|\U0001F382|农历|農曆",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Birthday:
    name: str
    on: date
    source: str
    note: str


def _birthday_text_matches(title: str, calendar: str) -> bool:
    """Crude on purpose: a false positive is a line the owner can ignore."""
    return bool(_BIRTHDAY_WORDS.search(title) or _BIRTHDAY_WORDS.search(calendar))


def _person_from_title(title: str) -> str:
    name = _TITLE_NOISE.sub("", title)
    name = re.sub(r"[（(][^()（）]*[)）]", "", name)
    name = name.strip(" \u00b7-|,，。:：[]【】")
    return " ".join(name.split()) or title.strip() or "?"


def _next_date(today: date, month: int, day: int) -> date | None:
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


def _required(arguments: dict[str, Any], name: str) -> str:
    value = str(arguments.get(name) or "").strip()
    if not value:
        raise PhoneToolError(f"{name} is required")
    return value


def _parse_datetime(value: Any, timezone_: ZoneInfo) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise PhoneToolError(
                f"not an ISO-8601 date-time: {value!r}"
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone_)
    return parsed


def _as_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
