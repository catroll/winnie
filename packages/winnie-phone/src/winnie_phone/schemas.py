"""Wire models for the phone-data capability.

The client posts one kind per request. Kind-specific items are validated here,
so a malformed calendar row cannot land in the calls table and a typo in a
field name fails at the edge instead of being ignored.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

SyncKind = Literal[
    "calendar",
    "call",
    "message",
    "contact",
    "notification",
    "location",
    "app",
    "device",
]
Direction = Literal[
    "incoming",
    "outgoing",
    "missed",
    "rejected",
    "blocked",
    "voicemail",
    "unknown",
]
MessageDirection = Literal["inbox", "sent", "draft", "outbox", "failed", "queued"]
CommandKind = Literal[
    "calendar.create",
    "calendar.update",
    "calendar.delete",
    "sms.send",
]

# A Java ``TimeZone.toString()`` carries its Olson id as ``id=...``. Some OEM
# calendar providers store that whole dump in ``Events.EVENT_TIMEZONE``.
_JAVA_TIMEZONE_ID = re.compile(r"\bid=([^,\]]+)")


def normalize_timezone(value: Any) -> Any:
    """Reduce a Java time-zone dump to the Olson id it contains.

    A Huawei/HarmonyOS calendar provider was observed storing
    ``java.util.SimpleTimeZone[id=Asia/Shanghai,offset=28800000,...]`` (267
    characters) where an id belongs. That is a provider representation, not a
    client error, and an event is worth more than its timezone label: rejecting
    the row would drop the event and, with ``mode=full``, could delete it.
    Anything that is not a known dump is returned unchanged so the length guard
    still catches genuinely malformed input.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if "java." not in stripped:
        return stripped
    match = _JAVA_TIMEZONE_ID.search(stripped)
    return match.group(1).strip() if match else stripped


class CalendarItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=64)
    calendar_id: str = Field(default="", max_length=64)
    calendar_name: str = Field(default="", max_length=256)
    title: str = Field(default="", max_length=2000)
    description: str = ""
    location: str = Field(default="", max_length=1000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    all_day: bool = False
    timezone: str = Field(default="", max_length=64)
    recurrence: str = ""
    organizer: str = Field(default="", max_length=320)
    attendees: list[str] = Field(default_factory=list)
    status: str = Field(default="", max_length=32)
    updated_at: datetime | None = None

    @field_validator("timezone", mode="before")
    @classmethod
    def _timezone(cls, value: Any) -> Any:
        return normalize_timezone(value)


class CallItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=64)
    number: str = Field(default="", max_length=128)
    cached_name: str = Field(default="", max_length=256)
    direction: Direction = "unknown"
    started_at: datetime | None = None
    duration_seconds: int = Field(default=0, ge=0)
    sim_account: str = Field(default="", max_length=128)
    is_new: bool = False


class MessageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=64)
    thread_id: str = Field(default="", max_length=64)
    address: str = Field(default="", max_length=128)
    direction: MessageDirection = "inbox"
    body: str = ""
    subject: str = Field(default="", max_length=512)
    received_at: datetime | None = None
    is_read: bool = True
    kind: Literal["sms", "mms"] = "sms"


class ContactItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=512)
    numbers: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    organization: str = Field(default="", max_length=512)
    starred: bool = False


class NotificationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=128)
    package_name: str = Field(default="", max_length=256)
    app_label: str = Field(default="", max_length=256)
    title: str = Field(default="", max_length=1000)
    text: str = ""
    category: str = Field(default="", max_length=64)
    posted_at: datetime | None = None
    is_ongoing: bool = False
    is_clearable: bool = True


class LocationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=128)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    altitude_m: float | None = None
    speed_mps: float | None = None
    provider: str = Field(default="", max_length=32)
    captured_at: datetime | None = None


class AppItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=256)
    label: str = Field(default="", max_length=512)
    package_name: str = Field(default="", max_length=256)
    version_name: str = Field(default="", max_length=128)
    version_code: int = 0
    is_system: bool = False
    is_enabled: bool = True
    installed_at: datetime | None = None
    updated_at: datetime | None = None


class DeviceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=128)
    manufacturer: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=128)
    os_version: str = Field(default="", max_length=64)
    app_version: str = Field(default="", max_length=32)
    timezone: str = Field(default="", max_length=64)
    scopes: list[str] = Field(default_factory=list)
    info: dict[str, Any] = Field(default_factory=dict)


ITEM_MODELS: dict[str, type[BaseModel]] = {
    "calendar": CalendarItem,
    "call": CallItem,
    "message": MessageItem,
    "contact": ContactItem,
    "notification": NotificationItem,
    "location": LocationItem,
    "app": AppItem,
    "device": DeviceSnapshot,
}


class DeviceRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_key: str = Field(min_length=8, max_length=64)
    name: str = Field(default="", max_length=128)
    platform: str = Field(default="android", max_length=32)
    manufacturer: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=128)
    os_version: str = Field(default="", max_length=64)
    app_version: str = Field(default="", max_length=32)
    timezone: str = Field(default="", max_length=64)
    scopes: list[str] = Field(default_factory=list)
    info: dict[str, Any] = Field(default_factory=dict)


class SyncStateRead(BaseModel):
    kind: str
    cursor: str = ""
    last_sync_at: datetime | None = None
    full_sync_at: datetime | None = None
    last_error: str = ""
    accepted_total: int = 0


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    device_key: str
    name: str
    platform: str
    manufacturer: str
    model: str
    os_version: str
    app_version: str
    timezone: str
    scopes: list[str]
    info: dict[str, Any]
    revoked: bool
    last_seen_at: datetime | None
    last_sync_at: datetime | None
    created_at: datetime
    states: list[SyncStateRead] = Field(default_factory=list)


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SyncKind
    # "full" means the items are a complete snapshot of the phone's current
    # view for this kind; anything the server holds but the snapshot omits is
    # marked deleted. "incremental" only upserts and applies ``deleted``.
    mode: Literal["incremental", "full"] = "incremental"
    cursor: str = Field(default="", max_length=64)
    items: list[dict[str, Any]] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    # A full snapshot may cover only a window (calendar). Deletions are then
    # inferred only for rows whose time falls inside the window.
    since: datetime | None = None
    until: datetime | None = None
    device: DeviceSnapshot | None = None


class SyncResult(BaseModel):
    kind: str
    accepted: int
    deleted: int
    cursor: str
    server_time: datetime
    interval_minutes: int


class CalendarCreatePayload(BaseModel):
    """What the phone needs to insert one event."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=2000)
    starts_at: datetime
    ends_at: datetime | None = None
    all_day: bool = False
    location: str = Field(default="", max_length=1000)
    description: str = ""
    calendar_id: str = Field(default="", max_length=64)
    calendar_name: str = Field(default="", max_length=256)
    timezone: str = Field(default="", max_length=64)


class CalendarUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=2000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=1000)
    description: str | None = None


class CalendarDeletePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)


class SmsSendPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=2000)


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "calendar.create": CalendarCreatePayload,
    "calendar.update": CalendarUpdatePayload,
    "calendar.delete": CalendarDeletePayload,
    "sms.send": SmsSendPayload,
}


class CommandPropose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CommandKind
    device_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    device_id: UUID
    kind: str
    payload: dict[str, Any]
    summary: str
    status: str
    result: str
    error: str
    expires_at: datetime | None
    approved_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["succeeded", "failed"]
    result: str = ""
    error: str = ""


class CalendarRead(BaseModel):
    id: str
    device_id: str
    device_name: str
    calendar_name: str
    title: str
    description: str
    location: str
    starts_at: datetime | None
    ends_at: datetime | None
    all_day: bool
    timezone: str
    recurrence: str = ""
    organizer: str
    attendees: list[str]
    status: str


class CallRead(BaseModel):
    id: str
    device_id: str
    device_name: str
    number: str
    cached_name: str
    direction: str
    started_at: datetime | None
    duration_seconds: int
    is_new: bool


class MessageRead(BaseModel):
    id: str
    device_id: str
    device_name: str
    address: str
    direction: str
    body: str
    subject: str
    received_at: datetime | None
    is_read: bool
    kind: str


class NotificationRead(BaseModel):
    id: str
    device_id: str
    device_name: str
    package_name: str
    app_label: str
    title: str
    text: str
    category: str
    posted_at: datetime | None
    is_ongoing: bool


class LocationRead(BaseModel):
    id: str
    device_id: str
    device_name: str
    latitude: float
    longitude: float
    accuracy_m: float | None
    provider: str
    captured_at: datetime | None


class AppRead(BaseModel):
    id: str
    device_id: str
    device_name: str
    package_name: str
    label: str
    version_name: str
    is_system: bool
    is_enabled: bool
    installed_at: datetime | None
    updated_at: datetime | None


class ContactRead(BaseModel):
    id: str
    device_id: str
    device_name: str
    display_name: str
    numbers: list[str]
    emails: list[str]
    organization: str
    starred: bool


class SearchHit(BaseModel):
    kind: str
    at: datetime | None
    title: str
    text: str
    device_id: str
    device_name: str
    record_id: str


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    device_names: dict[str, str] = Field(default_factory=dict)


class KindCount(BaseModel):
    kind: str
    count: int


class DeviceStatus(BaseModel):
    device: DeviceRead
    counts: list[KindCount]


class SummaryRead(BaseModel):
    devices: list[DeviceStatus] = Field(default_factory=list)
    counts: list[KindCount] = Field(default_factory=list)
    last_sync_at: datetime | None = None
    stale_minutes: int | None = None
    pending_commands: int = 0
