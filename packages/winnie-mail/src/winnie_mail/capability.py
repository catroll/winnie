"""The mailbox settings as a host-registered capability."""

from __future__ import annotations

from winnie_core.capability import Capability
from winnie_mail.router import router


def capability() -> Capability:
    return Capability(name="mail", router=router, prefix="/api/v1")
