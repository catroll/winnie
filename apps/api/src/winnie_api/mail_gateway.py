"""The mail capability as the assistant's read-only data source.

The assistant asks for mail through this adapter, never by importing the mail
package. It searches the local cache; when the cache is older than a few minutes
it refreshes first, so a question about "my email" is answered from mail that is
recent rather than from whenever the console last looked.

A failed refresh is not a failed answer: the search still returns what is
cached, and the tool reports the cache age.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from winnie_mail.cache import MailCache
from winnie_mail.client import MailError
from winnie_mail.models import iso_utc
from winnie_mail.service import MailService

logger = logging.getLogger(__name__)

OwnerResolver = Callable[[], Awaitable[str | None]]


class MailGatewayAdapter:
    """Adapts :class:`MailService` to the assistant's ``MailGateway`` protocol."""

    def __init__(
        self,
        service: MailService,
        *,
        resolve_owner: OwnerResolver,
        stale_after: timedelta = timedelta(minutes=15),
    ) -> None:
        self.service = service
        self.resolve_owner = resolve_owner
        self.stale_after = stale_after

    async def _owner(self) -> str | None:
        owner = await self.resolve_owner()
        return owner or None

    async def _refresh_if_stale(self, user_id: str) -> None:
        try:
            last = await self.service.last_synced(user_id)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if last is not None and now - last < self.stale_after:
                return
            await self.service.sync(user_id)
        except MailError as exc:
            # Read-only means read-only: a mailbox that is offline or rate-limited
            # must still answer from the cache.
            logger.info("mail refresh before search failed: %s", exc)

    async def search(
        self,
        query: str,
        *,
        folder: str = "",
        unread_only: bool = False,
        limit: int = 10,
    ) -> dict[str, Any]:
        owner = await self._owner()
        if owner is None:
            return {"synced_at": "", "messages": []}
        await self._refresh_if_stale(owner)
        messages = await self.service.messages(
            owner,
            folder=folder,
            query=query,
            unread=unread_only or None,
            limit=max(1, min(limit, 20)),
        )
        last = await self.service.last_synced(owner)
        return {
            "synced_at": iso_utc(last),
            "messages": messages,
        }

    async def message(self, message_id: str) -> dict[str, Any] | None:
        owner = await self._owner()
        if owner is None:
            return None
        return await self.service.message(owner, message_id)


__all__ = ["MailCache", "MailGatewayAdapter"]
