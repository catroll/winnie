"""One user's mailbox settings, and the file that holds them.

The store is a mode ``0600`` YAML file per account under ``data/mail``, the same
convention the gateway keys and the vendor API keys already use. A mailbox
password must be readable by the server to log in, so it cannot be hashed; the
file's permissions are the protection, and the API never returns the value.

The layout is one ``mail`` section so a later settings section (timezone,
language, notification preferences) can share the file without a migration.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Security = Literal["ssl", "starttls", "none"]
Protocol = Literal["imap", "pop3", "none"]

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Conventional ports, used when the caller leaves the field at its default but
# names a different security mode: 587 is STARTTLS, 465 implicit TLS.
DEFAULT_SMTP_PORTS = {"ssl": 465, "starttls": 587, "none": 25}
DEFAULT_IMAP_PORTS = {"ssl": 993, "starttls": 143, "none": 143}
DEFAULT_POP3_PORTS = {"ssl": 995, "starttls": 110, "none": 110}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SmtpSettings(BaseModel):
    """Outgoing mail. ``password`` is the mailbox password or app password."""

    model_config = ConfigDict(extra="forbid")

    host: str = ""
    port: int = Field(default=465, ge=1, le=65535)
    security: Security = "ssl"
    username: str = ""
    password: str = ""
    from_address: str = ""
    from_name: str = ""


class ReceiveSettings(BaseModel):
    """Incoming mail. ``protocol = none`` keeps the mailbox send-only.

    ``use_send_credentials`` is the common case (one address, one password);
    turning it off allows a receive login that differs from the SMTP one.
    """

    model_config = ConfigDict(extra="forbid")

    protocol: Protocol = "imap"
    host: str = ""
    port: int = Field(default=993, ge=1, le=65535)
    security: Security = "ssl"
    username: str = ""
    password: str = ""
    use_send_credentials: bool = True
    folder: str = "INBOX"


class MailSettings(BaseModel):
    """The whole account as stored. Never returned by the API as-is."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    address: str = ""
    display_name: str = ""
    smtp: SmtpSettings = Field(default_factory=SmtpSettings)
    receive: ReceiveSettings = Field(default_factory=ReceiveSettings)
    max_messages: int = Field(default=20, ge=1, le=200)
    timeout_seconds: float = Field(default=20.0, ge=3.0, le=120.0)
    updated_at: str = ""

    @field_validator("address", "display_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    # -- derived values ---------------------------------------------------

    def smtp_from(self) -> str:
        """The address the message is sent as: ``from_address`` or ``address``."""
        return (self.smtp.from_address or self.address).strip()

    def receive_login(self) -> tuple[str, str]:
        """The receive username and password, honouring the shared-credential flag."""
        if self.receive.use_send_credentials:
            return (
                (self.smtp.username or self.address).strip(),
                self.smtp.password,
            )
        return self.receive.username.strip(), self.receive.password

    def can_send(self) -> bool:
        return bool(self.enabled and self.smtp.host and self.smtp_from())

    def can_receive(self) -> bool:
        return bool(self.receive.protocol != "none" and self.receive.host)

    def problems(self) -> list[str]:
        """What is missing or inconsistent, in the owner's words.

        Returning a list rather than raising keeps the form usable: the UI can
        save a half-filled account and fix it later, while a send or a test
        refuses with the exact missing field.
        """
        problems: list[str] = []
        if not self.enabled:
            return problems
        if not self.address:
            problems.append("address is required")
        if not self.smtp.host:
            problems.append("smtp.host is required")
        if self.receive.protocol != "none":
            if not self.receive.host:
                problems.append("receive.host is required")
            if not self.receive.use_send_credentials and not self.receive.username:
                problems.append("receive.username is required")
        return problems


class MailSettingsStore:
    """Reads and writes ``<root>/<user_id>.yaml``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:  # pragma: no cover - a pre-existing directory may be managed
            pass

    def path(self, user_id: str) -> Path:
        if not SAFE_NAME.match(user_id):
            # A user id reaches this from a URL: it must never name a path.
            raise ValueError(f"unsafe user id: {user_id!r}")
        return self.root / f"{user_id}.yaml"

    def load(self, user_id: str) -> MailSettings:
        return self._read(self.path(user_id))

    def _read(self, path: Path) -> MailSettings:
        if not path.exists():
            return MailSettings()
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return MailSettings()
        if not isinstance(raw, dict):
            return MailSettings()
        section = raw.get("mail")
        if not isinstance(section, dict):
            return MailSettings()
        try:
            return MailSettings.model_validate(section)
        except ValidationError:
            return MailSettings()

    def save(self, user_id: str, settings: MailSettings) -> MailSettings:
        path = self.path(user_id)
        settings = settings.model_copy(update={"updated_at": utcnow()})
        payload = {"version": 1, "mail": settings.model_dump(mode="json")}
        text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        directory = path.parent
        directory.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=directory, prefix=".mail-", suffix=".yaml")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return settings

    def any_account(self) -> bool:
        """Whether a saved account can send, i.e. whether email is a real rung."""
        return any(self._read(path).can_send() for path in self.root.glob("*.yaml"))

    def clear(self, user_id: str) -> bool:
        path = self.path(user_id)
        if not path.exists():
            return False
        path.unlink()
        return True
