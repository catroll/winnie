"""Winnie mail: one mailbox per account, configured by the owner.

The package owns three things: the per-user settings file, the SMTP/IMAP/POP3
clients that use it, and the console endpoints that edit it. The proactive
engine reaches the same account through :class:`MailService`, so a mailbox is
configured once and every rung that needs it finds it.
"""

from __future__ import annotations

from winnie_mail.client import (
    MailAuthError,
    MailError,
    MailMessage,
    MailNotConfigured,
    MailTransportError,
)
from winnie_mail.config import MailConfig
from winnie_mail.service import MailService
from winnie_mail.settings import MailSettings, MailSettingsStore

__all__ = [
    "MailAuthError",
    "MailConfig",
    "MailError",
    "MailMessage",
    "MailNotConfigured",
    "MailService",
    "MailSettings",
    "MailSettingsStore",
    "MailTransportError",
]
