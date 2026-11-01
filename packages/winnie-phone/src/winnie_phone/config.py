"""Configuration for the phone-data capability.

Phone data is the most sensitive store in the project, so every retention
window and every size limit is explicit here rather than hidden in the code.
A value of 0 means "keep everything"; delete-on-retention is applied when a
device syncs, so no separate scheduler is needed for a single-owner instance.
"""

from __future__ import annotations

from typing import Literal

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


class PhoneGeocodeConfig(BaseModel):
    """Reverse geocoding of location fixes, done on the server.

    Disabled by default: turning this on sends the owner's coordinates to a
    third party, so it is an explicit choice. ``photon`` needs no credential at
    all; ``amap`` reads one from the environment, never from a config file.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # ``photon`` is keyless and answers with the nearest named OSM feature;
    # ``amap`` resolves postal addresses and needs a key. The name is a Literal
    # so a typo fails at startup instead of resolving nothing for ever.
    provider: Literal["amap", "photon"] = "amap"
    # Environment variable holding the provider key; only ``amap`` reads it.
    key_env: str = "AMAP_API_KEY"
    # The datum the *phone* reports its fixes in. Android's LocationManager hands
    # out WGS-84; ``amap`` wants GCJ-02 and the server converts. Set this to
    # ``gcj02`` only for a client that already reports the offset datum.
    coordinate_system: Literal["wgs84", "gcj02"] = "wgs84"
    timeout_seconds: float = Field(default=2.5, gt=0, le=10)
    # Minimum gap between two provider calls; a personal key has a low ceiling.
    min_interval_seconds: float = Field(default=1.0, ge=0, le=10)
    # Cached coordinates (rounded to ~111 m) kept in memory.
    cache_entries: int = Field(default=512, ge=0, le=10000)
    # Fixes geocoded per sync; a normal sync carries one, a backfill many.
    max_per_sync: int = Field(default=5, ge=0, le=50)


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
    geocode: PhoneGeocodeConfig = Field(default_factory=PhoneGeocodeConfig)

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
