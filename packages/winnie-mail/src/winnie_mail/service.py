"""The mailbox as the rest of Winnie uses it: settings in, mail in and out.

Every call names the account it acts for, because the settings are per user and
the store is keyed by user id. The blocking protocol calls run in a worker
thread, so a slow mailbox cannot stall the API's event loop.

Three surfaces share this service:

* **Sending** — one message through the stored SMTP account.
* **Syncing** — every selectable folder, headers only, merged into the cache
  with a cursor per folder so the next pass only asks for what is new.
* **Reading** — list, search and open a message. A body is downloaded on the
  first open and cached after that, so clicking twice costs one round trip.
"""

from __future__ import annotations

import asyncio

from winnie_mail import client
from winnie_mail.cache import MailCache
from winnie_mail.schemas import MailUpdate, MailView
from winnie_mail.settings import (
    MailSettings,
    MailSettingsStore,
    ReceiveSettings,
    SmtpSettings,
)


class MailService:
    def __init__(
        self,
        store: MailSettingsStore,
        *,
        cache: MailCache | None = None,
        timeout_seconds: float = 20.0,
        per_folder_limit: int = 50,
    ) -> None:
        self.store = store
        self.cache = cache
        self.default_timeout_seconds = timeout_seconds
        self.per_folder_limit = per_folder_limit

    def attach_cache(self, cache: MailCache) -> None:
        """The host attaches the database-backed cache once the database exists."""
        self.cache = cache

    # -- settings ---------------------------------------------------------

    def load(self, user_id: str) -> MailSettings:
        return self.store.load(user_id)

    def has_accounts(self) -> bool:
        return self.store.any_account()

    def view(self, user_id: str) -> MailView:
        return MailView.of(self.load(user_id))

    def apply(self, user_id: str, update: MailUpdate) -> MailView:
        """Replace the account, keeping a password the caller did not resend.

        ``None`` means "unchanged" so the console can save the form without ever
        holding the secret; the empty string deliberately clears it.
        """
        current = self.load(user_id)
        smtp_password = (
            current.smtp.password if update.smtp.password is None else update.smtp.password
        )
        receive_password = (
            current.receive.password
            if update.receive.password is None
            else update.receive.password
        )
        timeout = update.timeout_seconds or self.default_timeout_seconds
        settings = MailSettings(
            enabled=update.enabled,
            address=update.address.strip(),
            display_name=update.display_name.strip(),
            smtp=SmtpSettings(
                host=update.smtp.host.strip(),
                port=update.smtp.port,
                security=update.smtp.security,
                username=update.smtp.username.strip(),
                password=smtp_password,
                from_address=update.smtp.from_address.strip(),
                from_name=update.smtp.from_name.strip(),
            ),
            receive=ReceiveSettings(
                protocol=update.receive.protocol,
                host=update.receive.host.strip(),
                port=update.receive.port,
                security=update.receive.security,
                username=update.receive.username.strip(),
                password=receive_password,
                use_send_credentials=update.receive.use_send_credentials,
                folder=(update.receive.folder or "INBOX").strip(),
            ),
            max_messages=update.max_messages,
            timeout_seconds=timeout,
        )
        return MailView.of(self.store.save(user_id, settings))

    async def clear(self, user_id: str) -> bool:
        removed = self.store.clear(user_id)
        if self.cache is not None:
            await self.cache.purge(user_id)
        return removed

    # -- protocol calls ---------------------------------------------------

    async def send(self, user_id: str, *, to: str, subject: str, body: str) -> str:
        settings = self.load(user_id)
        recipient = (to or settings.address).strip()
        return await asyncio.to_thread(
            client.send,
            settings,
            to=recipient,
            subject=subject,
            body=body,
        )

    async def test(self, user_id: str, target: str) -> str:
        settings = self.load(user_id)
        if target == "smtp":
            return await asyncio.to_thread(client.test_smtp, settings)
        return await asyncio.to_thread(client.test_receive, settings)

    # -- the cache ---------------------------------------------------------

    def _require_cache(self) -> MailCache:
        if self.cache is None:
            raise client.MailUnavailable(
                "mail browsing needs the application database, which this "
                "deployment has not initialised"
            )
        return self.cache

    async def folders(self, user_id: str) -> dict:
        settings = self.load(user_id)
        cache = self._require_cache()
        return {
            "protocol": settings.receive.protocol,
            "folders": await cache.folders(user_id, settings.receive.protocol),
            "stats": await cache.stats(user_id),
        }

    async def sync(self, user_id: str) -> dict:
        """Walk every folder and merge what is new since each cursor.

        Two logins per pass: one to list the folders, one to read them all. The
        per-folder cursor lives in the cache, so a second pass downloads only
        what arrived in between.
        """
        settings = self.load(user_id)
        if not settings.can_receive():
            raise client.MailNotConfigured(
                "; ".join(settings.problems()) or "receiving is not enabled"
            )
        cache = self._require_cache()
        protocol = settings.receive.protocol
        folders = await asyncio.to_thread(client.list_folders, settings)
        await cache.prune_folders(user_id, protocol, {folder.name for folder in folders})
        plans = []
        for folder in folders:
            cached = await cache.folder(user_id, protocol, folder.name) or {}
            known = await cache.known_uids(user_id, protocol, folder.name)
            plans.append(
                client.FolderPlan(
                    folder=folder,
                    known_uids=frozenset(known),
                    last_uid=int(cached.get("last_uid") or 0),
                    uid_validity=int(cached.get("uid_validity") or 0),
                )
            )
        results = await asyncio.to_thread(
            client.sync_all, settings, plans, limit=self.per_folder_limit
        )
        fetched = 0
        errors: list[dict] = []
        for result in results:
            if result.snapshot is None:
                errors.append({"folder": result.folder.name, "error": result.error})
                await cache.record_error(
                    user_id, protocol, result.folder, result.error
                )
                continue
            await cache.apply(user_id, result.snapshot, protocol=protocol)
            fetched += len(result.snapshot.fetched)
        return {
            "protocol": protocol,
            "folders": len(results),
            "fetched": fetched,
            "errors": errors,
            "stats": await cache.stats(user_id),
        }

    async def stats(self, user_id: str) -> dict:
        return await self._require_cache().stats(user_id)

    async def last_synced(self, user_id: str):
        return await self._require_cache().last_synced(user_id)

    async def messages(
        self,
        user_id: str,
        *,
        folder: str = "",
        query: str = "",
        unread: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        return await self._require_cache().list_messages(
            user_id,
            folder=folder,
            query=query,
            unread=unread,
            limit=limit,
            offset=offset,
        )

    async def search(self, user_id: str, query: str, *, limit: int = 20) -> list[dict]:
        return await self.messages(user_id, query=query, limit=limit)

    async def message(self, user_id: str, message_id: str) -> dict | None:
        """One cached message, downloading its body the first time it is opened."""
        cache = self._require_cache()
        row = await cache.message(user_id, message_id)
        if row is None:
            return None
        if not row.get("has_body"):
            settings = self.load(user_id)
            if not settings.can_receive():
                return row
            fetched = await asyncio.to_thread(
                client.fetch_body,
                settings,
                folder=row["folder"],
                uid=row["uid"],
            )
            updated = await cache.store_body(user_id, message_id, fetched)
            return updated or row
        return row


__all__ = ["MailService"]
