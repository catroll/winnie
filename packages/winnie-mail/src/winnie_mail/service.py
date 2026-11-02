"""The mailbox as the rest of Winnie uses it: settings in, mail in and out.

Every call names the account it acts for, because the settings are per user and
the store is keyed by user id. The blocking protocol calls run in a worker
thread, so a slow mailbox cannot stall the API's event loop.
"""

from __future__ import annotations

import asyncio

from winnie_mail import client
from winnie_mail.schemas import MailUpdate, MailView
from winnie_mail.settings import (
    MailSettings,
    MailSettingsStore,
    ReceiveSettings,
    SmtpSettings,
)


class MailService:
    def __init__(self, store: MailSettingsStore, *, timeout_seconds: float = 20.0) -> None:
        self.store = store
        self.default_timeout_seconds = timeout_seconds

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

    def clear(self, user_id: str) -> bool:
        return self.store.clear(user_id)

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

    async def recent(self, user_id: str, *, limit: int = 10) -> list[client.MailMessage]:
        settings = self.load(user_id)
        return await asyncio.to_thread(client.fetch_recent, settings, limit=limit)

    async def test(self, user_id: str, target: str) -> str:
        settings = self.load(user_id)
        if target == "smtp":
            return await asyncio.to_thread(client.test_smtp, settings)
        return await asyncio.to_thread(client.test_receive, settings)


__all__ = ["MailService"]
