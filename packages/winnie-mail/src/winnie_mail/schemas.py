"""Request and response shapes for the mailbox settings endpoints.

The read models carry ``password_set`` instead of a password. The update models
accept ``password`` on the write path only, where ``None`` means "keep what is
stored" and the empty string means "clear it".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from winnie_mail.settings import MailSettings, Protocol, Security

TestTarget = Literal["smtp", "receive"]


class SmtpView(BaseModel):
    host: str = ""
    port: int = 465
    security: Security = "ssl"
    username: str = ""
    from_address: str = ""
    from_name: str = ""
    password_set: bool = False


class ReceiveView(BaseModel):
    protocol: Protocol = "imap"
    host: str = ""
    port: int = 993
    security: Security = "ssl"
    username: str = ""
    use_send_credentials: bool = True
    folder: str = "INBOX"
    password_set: bool = False


class MailView(BaseModel):
    enabled: bool = False
    address: str = ""
    display_name: str = ""
    smtp: SmtpView = Field(default_factory=SmtpView)
    receive: ReceiveView = Field(default_factory=ReceiveView)
    max_messages: int = 20
    timeout_seconds: float = 20.0
    updated_at: str = ""
    can_send: bool = False
    can_receive: bool = False

    @classmethod
    def of(cls, settings: MailSettings) -> MailView:
        return cls(
            enabled=settings.enabled,
            address=settings.address,
            display_name=settings.display_name,
            smtp=SmtpView(
                host=settings.smtp.host,
                port=settings.smtp.port,
                security=settings.smtp.security,
                username=settings.smtp.username,
                from_address=settings.smtp.from_address,
                from_name=settings.smtp.from_name,
                password_set=bool(settings.smtp.password),
            ),
            receive=ReceiveView(
                protocol=settings.receive.protocol,
                host=settings.receive.host,
                port=settings.receive.port,
                security=settings.receive.security,
                username=settings.receive.username,
                use_send_credentials=settings.receive.use_send_credentials,
                folder=settings.receive.folder,
                password_set=bool(settings.receive.password),
            ),
            max_messages=settings.max_messages,
            timeout_seconds=settings.timeout_seconds,
            updated_at=settings.updated_at,
            can_send=settings.can_send(),
            can_receive=settings.can_receive(),
        )


class SmtpUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = ""
    port: int = Field(default=465, ge=1, le=65535)
    security: Security = "ssl"
    username: str = ""
    # None keeps the stored secret; "" clears it; any other value replaces it.
    password: str | None = None
    from_address: str = ""
    from_name: str = ""


class ReceiveUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Protocol = "imap"
    host: str = ""
    port: int = Field(default=993, ge=1, le=65535)
    security: Security = "ssl"
    username: str = ""
    password: str | None = None
    use_send_credentials: bool = True
    folder: str = "INBOX"


class MailUpdate(BaseModel):
    """A full replacement of the non-secret fields.

    The form always sends every field, so a PUT that omits one means "empty",
    not "unchanged" — the exception is a password, which is never rendered.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    address: str = ""
    display_name: str = ""
    smtp: SmtpUpdate = Field(default_factory=SmtpUpdate)
    receive: ReceiveUpdate = Field(default_factory=ReceiveUpdate)
    max_messages: int = Field(default=20, ge=1, le=200)
    timeout_seconds: float = Field(default=20.0, ge=3.0, le=120.0)


class MailTestRequest(BaseModel):
    target: TestTarget = "smtp"


class MailTestResult(BaseModel):
    ok: bool
    target: TestTarget
    detail: str


class MailSendRequest(BaseModel):
    to: str = ""
    subject: str = ""
    body: str = ""


class MailSendResult(BaseModel):
    ok: bool
    receipt: str


class MailStats(BaseModel):
    messages: int = 0
    unread: int = 0
    folders: int = 0


class MailFolderView(BaseModel):
    name: str
    display_name: str
    delimiter: str = ""
    selectable: bool = True
    protocol: str = "imap"
    uid_validity: int = 0
    last_uid: int = 0
    total: int = 0
    unread: int = 0
    last_synced_at: str = ""
    last_error: str = ""


class MailFolderList(BaseModel):
    protocol: Protocol = "imap"
    folders: list[MailFolderView] = []
    stats: MailStats = Field(default_factory=MailStats)


class MailCacheMessage(BaseModel):
    """A cached message; ``body`` is empty until someone opens it."""

    id: str
    folder: str
    folder_display: str = ""
    protocol: str
    uid: str
    subject: str
    sender: str
    recipients: str = ""
    date: str = ""
    snippet: str = ""
    unread: bool = False
    size: int = 0
    has_body: bool = False
    message_id: str = ""
    body: str = ""


class MailSyncResult(BaseModel):
    protocol: Protocol = "imap"
    folders: int = 0
    fetched: int = 0
    errors: list[dict[str, str]] = []
    stats: MailStats = Field(default_factory=MailStats)
