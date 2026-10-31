"""Gateway as a host-registered capability."""

from __future__ import annotations

from winnie_core.capability import Capability
from winnie_gateway.router import router


def capability() -> Capability:
    return Capability(name="gateway", router=router)
