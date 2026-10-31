"""Push as a host-registered capability."""

from __future__ import annotations

from winnie_core.capability import Capability
from winnie_push.router import router


def capability() -> Capability:
    return Capability(name="push", router=router, prefix="/api/v1")
