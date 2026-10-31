"""Configuration for the phone-data capability.

Phone data is the most sensitive store in the project, so every retention
window and every size limit is explicit here rather than hidden in the code.
A value of 0 means "keep everything"; delete-on-retention is applied when a
device syncs, so no separate scheduler is needed for a single-owner instance.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

KINDS = (
    "calendar",
    "call",
    "message",
    "contact",
    "notification",
    "location",
    "app",
)


class PhoneRetentionConfig(BaseModel):
    """How long each kind of record stays on the server.

    Ambient readings age out quickly; the structured records the assistant
    reasons about are kept long by default because the phone may not keep them
    that long either. Contacts and apps are snapshots rather than history, so
    they are replaced by the next full sync instead of expiring.
    """

    model_config = ConfigDict(extra="forbid")

    calendar_days: int = Field(default=730, ge=0)
    call_days: int = Field(default=730, ge=0)
    message_days: int = Field(default=365, ge=0)
    contact_days: int = Field(default=0, ge=0)
    notification_days: int = Field(default=14, ge=0)
    location_days: int = Field(default=30, ge=0)
    app_days: int = Field(default=0, ge=0)


class PhoneConfig(BaseModel):
    """Deployment settings owned by the host."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Whose phone the assistant reads when the instance has more than one
    # account. Empty means "the only active account", or the local user when
    # accounts are disabled.
    owner_user: str = ""
    # Hint returned to the client. Android's own minimum is 15 minutes.
    sync_interval_minutes: int = Field(default=30, ge=5, le=1440)
    # One sync request carries at most this many items of one kind. The client
    # pages larger histories.
    max_batch_items: int = Field(default=200, ge=1, le=2000)
    # Long text is truncated before it is stored, so one SMS or notification
    # cannot make a row unbounded.
    max_text_chars: int = Field(default=8000, ge=100, le=100000)
    # A proposed write expires if the owner never confirms it.
    command_ttl_seconds: int = Field(default=900, ge=30, le=86400)
    # Commands handed to a device in one poll.
    max_commands_per_poll: int = Field(default=10, ge=1, le=100)
    retention: PhoneRetentionConfig = Field(
        default_factory=PhoneRetentionConfig
    )

    def retention_days(self, kind: str) -> int:
        return {
            "calendar": self.retention.calendar_days,
            "call": self.retention.call_days,
            "message": self.retention.message_days,
            "contact": self.retention.contact_days,
            "notification": self.retention.notification_days,
            "location": self.retention.location_days,
            "app": self.retention.app_days,
        }.get(kind, 0)
