"""Phone data: sync what a phone can read, and queue the writes it executes.

The package owns the schema, the merge rules and the assistant-facing queries.
It does not own HTTP authentication, scheduling or the client: the host mounts
:mod:`winnie_phone.router`, and the Android app is the only writer of device
data.
"""

from __future__ import annotations

from winnie_phone.config import PhoneConfig, PhoneRetentionConfig
from winnie_phone.service import (
    CommandNotFound,
    DeviceNotFound,
    DeviceRevoked,
    InvalidCommand,
    PhoneError,
    PhoneEvents,
    PhoneService,
)

__all__ = [
    "CommandNotFound",
    "DeviceNotFound",
    "DeviceRevoked",
    "InvalidCommand",
    "PhoneConfig",
    "PhoneError",
    "PhoneEvents",
    "PhoneRetentionConfig",
    "PhoneService",
]
