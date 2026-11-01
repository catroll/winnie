"""Proactive interventions: notice, decide, say what to do, track delivery.

The public surface is small on purpose: the host builds a
:class:`~winnie_proactive.service.ProactiveService` with its data adapters and
channels, and mounts the router from :mod:`winnie_proactive.capability`.
"""

from __future__ import annotations

from winnie_proactive.config import ProactiveConfig
from winnie_proactive.ports import (
    CalendarPort,
    Channel,
    ChannelError,
    ContactsPort,
    Message,
    SchedulePort,
)
from winnie_proactive.service import ProactiveError, ProactiveService

__all__ = [
    "CalendarPort",
    "Channel",
    "ChannelError",
    "ContactsPort",
    "Message",
    "ProactiveConfig",
    "ProactiveError",
    "ProactiveService",
    "SchedulePort",
]
