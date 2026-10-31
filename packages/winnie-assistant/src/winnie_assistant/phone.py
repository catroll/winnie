"""The boundary between the assistant and the phone-data capability.

The assistant must not import ``winnie-phone``: capabilities do not know each
other. This protocol declares what the phone tools need, and the host adapts
the phone service to it. With no implementation the phone skills are reported
unavailable instead of being offered and failing.

Reads are ordinary queries. Writes are *proposals*: the host stores a command,
the assistant asks the owner to confirm, and only then does the phone execute
it on its next sync.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class PhoneGateway(Protocol):
    """What the assistant's phone tools may ask for."""

    async def summary(self) -> dict[str, Any]: ...

    async def search(
        self,
        query: str,
        *,
        kinds: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...

    async def calendar(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        query: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    async def calls(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        direction: str = "",
        query: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    async def messages(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        address: str = "",
        query: str = "",
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    async def contacts(
        self, *, query: str = "", limit: int = 50
    ) -> list[dict[str, Any]]: ...

    async def notifications(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        app: str = "",
        query: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    async def locations(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    async def apps(
        self, *, query: str = "", limit: int = 100
    ) -> list[dict[str, Any]]: ...

    async def propose_command(
        self, kind: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def approve_command(self, command_id: str) -> dict[str, Any]: ...

    async def cancel_command(self, command_id: str) -> dict[str, Any]: ...
