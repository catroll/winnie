"""Assistant tools over the owner's phone data.

The tools follow two rules that the model cannot be trusted to infer:

1. A phone is a *remote* writer. Every write is a proposal until the owner
   confirms it in chat, and even then the phone executes it on its next sync.
   So a tool never claims an action happened.
2. Synced data is a snapshot, not the live phone. Every answer carries the last
   sync time so the model can say how fresh it is.

Formatting lives here, next to the tools, so the assistant prompt and the tool
output use one time zone and one date format.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from winnie_assistant.phone import PhoneGateway

logger = logging.getLogger(__name__)


class PhoneToolError(RuntimeError):
    """A phone tool call that cannot be completed for a reportable reason."""


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
            "PROPOSE creating a calendar event on the phone. Nothing is written "
            "yet. Use an ISO-8601 start time with an offset, for example "
            "2026-11-02T15:00:00+08:00, and restate the local time when you ask "
            "for confirmation. After an explicit yes, call phone_confirm."
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
            lines.append(
                f"- {when} [id {event['id']}] {event.get('title') or '(untitled)'}"
                f"{location}{calendar}"
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
