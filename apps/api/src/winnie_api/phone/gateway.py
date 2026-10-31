"""Expose the phone service to the assistant through its declared protocol.

The assistant must not import ``winnie-phone``; this adapter is the only place
where the two meet. It also owns the question "whose phone?", which the phone
service deliberately does not answer: a device belongs to the account that
registered it, while the assistant is a single-owner service.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from winnie_phone.service import PhoneService


class PhoneGatewayAdapter:
    def __init__(
        self,
        service: PhoneService,
        *,
        resolve_owner: Callable[[], Awaitable[str]],
    ) -> None:
        self.service = service
        self._resolve_owner = resolve_owner
        # Cached after the first successful resolution, but never cached while
        # empty: a fresh install registers its account after startup.
        self._owner = ""

    async def _owner_id(self) -> str:
        if self._owner:
            return self._owner
        owner = (await self._resolve_owner()).strip()
        if not owner:
            raise LookupError(
                "phone data has no owner yet: register an account, or set "
                "phone.owner_user when the instance has more than one"
            )
        self._owner = owner
        return owner

    @staticmethod
    def _command_id(value: str) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError):
            raise LookupError(f"not a phone command id: {value}") from None

    async def summary(self) -> dict[str, Any]:
        return await self.service.summary(await self._owner_id())

    async def search(
        self,
        query: str,
        *,
        kinds: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return await self.service.search(
            await self._owner_id(),
            query,
            kinds=kinds,
            since=since,
            until=until,
            limit=limit,
        )

    async def calendar(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        query: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self.service.calendar(
            await self._owner_id(),
            since=since,
            until=until,
            query=query,
            limit=limit,
        )

    async def calls(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        direction: str = "",
        query: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self.service.calls(
            await self._owner_id(),
            since=since,
            until=until,
            direction=direction,
            query=query,
            limit=limit,
        )

    async def messages(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        address: str = "",
        query: str = "",
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self.service.messages(
            await self._owner_id(),
            since=since,
            until=until,
            address=address,
            query=query,
            unread_only=unread_only,
            limit=limit,
        )

    async def contacts(
        self, *, query: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        return await self.service.contacts(
            await self._owner_id(), query=query, limit=limit
        )

    async def notifications(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        app: str = "",
        query: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self.service.notifications(
            await self._owner_id(),
            since=since,
            until=until,
            app=app,
            query=query,
            limit=limit,
        )

    async def locations(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self.service.locations(
            await self._owner_id(), since=since, until=until, limit=limit
        )

    async def apps(
        self, *, query: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        return await self.service.apps(
            await self._owner_id(), query=query, limit=limit
        )

    async def propose_command(
        self, kind: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.service.propose_command(
            await self._owner_id(), kind, payload
        )

    async def approve_command(self, command_id: str) -> dict[str, Any]:
        return await self.service.approve_command(
            await self._owner_id(), self._command_id(command_id)
        )

    async def cancel_command(self, command_id: str) -> dict[str, Any]:
        return await self.service.cancel_command(
            await self._owner_id(), self._command_id(command_id)
        )
