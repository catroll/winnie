"""Winnie mail: one mailbox per account, configured by the owner.

The package owns four things: the per-user settings file, the SMTP/IMAP/POP3
clients that use it, a local cache of folder and message headers with an
incremental cursor per folder, and the endpoints over both. Other modules read
mail through :class:`MailService` — the console for browsing, the proactive
engine for the email rung, the assistant for search — so a mailbox is configured
once and every consumer finds it.
"""

from __future__ import annotations

from winnie_mail.cache import MailCache
from winnie_mail.client import (
    MailAuthError,
    MailError,
    MailMessage,
    MailNotConfigured,
    MailTransportError,
    MailUnavailable,
)
from winnie_mail.config import MailConfig
from winnie_mail.service import MailService
from winnie_mail.settings import MailSettings, MailSettingsStore

__all__ = [
    "MailAuthError",
    "MailCache",
    "MailConfig",
    "MailError",
    "MailMessage",
    "MailNotConfigured",
    "MailService",
    "MailSettings",
    "MailSettingsStore",
    "MailTransportError",
    "MailUnavailable",
]
