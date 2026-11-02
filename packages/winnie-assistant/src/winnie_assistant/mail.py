"""The boundary between the assistant and the mail capability.

The assistant must not import ``winnie-mail``: capabilities do not know each
other. This protocol declares the two read-only things the mail tools need, and
the host adapts the mail service to it. Sending is deliberately absent — an
outward message stays behind a route the owner triggers.

With no implementation the mail skill is reported unavailable instead of being
offered and failing.
"""

from __future__ import annotations

from typing import Any, Protocol


class MailGateway(Protocol):
    """What the assistant's mail tools may ask for."""

    async def search(
        self,
        query: str,
        *,
        folder: str = "",
        unread_only: bool = False,
        limit: int = 10,
    ) -> dict[str, Any]: ...

    async def message(self, message_id: str) -> dict[str, Any] | None: ...
