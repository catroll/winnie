"""Auth as a framework capability."""

from __future__ import annotations

from winnie_core.auth.router import router
from winnie_core.capability import Capability


def capability() -> Capability:
    return Capability(name="auth", router=router, prefix="/api/v1")
