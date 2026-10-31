"""Expose the push service to the assistant through its declared protocol."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from winnie_push.service import PushService


class PushNotifierAdapter:
    """Adapt ``PushService`` to ``winnie_assistant.notify.Notifier``.

    The assistant is a single-owner service, like the phone tools, so the
    target account is resolved by the host instead of being part of the tool.
    """

    def __init__(
        self,
        service: PushService,
        *,
        resolve_owner: Callable[[], Awaitable[str]],
    ) -> None:
        self.service = service
        self._resolve_owner = resolve_owner
        self._owner = ""

    async def _owner_id(self) -> str:
        if self._owner:
            return self._owner
        owner = (await self._resolve_owner()).strip()
        if not owner:
            raise LookupError(
                "notifications have no owner yet: register an account, or set "
                "phone.owner_user when the instance has more than one"
            )
        self._owner = owner
        return owner

    async def notify(
        self,
        *,
        title: str,
        body: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.service.notify(
            await self._owner_id(), title=title, body=body, data=data
        )
