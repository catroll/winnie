"""Configuration for the push capability."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PushConfig(BaseModel):
    """Settings for the durable notification stream.

    The stream is a WebSocket the Android client holds open from a foreground
    service. There is no vendor push service: the server writes a row, and a
    connected device receives it immediately; a device that was offline gets it
    from the backlog when it reconnects.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Notifications older than this are deleted even if never delivered; an old
    # reminder is worse than no reminder.
    retention_days: int = Field(default=30, ge=1, le=3650)
    # A reconnecting client receives at most this many backlog events; the
    # rest are skipped and the client continues from the newest one.
    max_backlog: int = Field(default=200, ge=1, le=5000)
    # Server heartbeat over the WebSocket, so a dead NAT mapping is noticed.
    heartbeat_seconds: int = Field(default=25, ge=5, le=300)
    # One slow client must not grow an unbounded queue in memory.
    max_queue: int = Field(default=100, ge=1, le=10000)
    # How often the retention pass runs while the API is up.
    prune_interval_seconds: int = Field(default=3600, ge=60, le=86400)
