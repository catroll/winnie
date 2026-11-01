"""Proactive as a host-registered capability."""

from __future__ import annotations

from winnie_core.capability import Capability
from winnie_proactive.router import router


def capability() -> Capability:
    return Capability(name="proactive", router=router, prefix="/api/v1")
