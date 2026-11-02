"""The protocol layer: SMTP out, IMAP and POP3 in.

Blocking stdlib clients live here and the service runs them in a worker thread.
Nothing in this module reads configuration or touches the settings store; it is
handed a :class:`MailSettings` and either returns a receipt or raises one of the
three errors below, so the HTTP layer can map a failure to a status code without
guessing.

Two provider quirks are handled explicitly because they look like authentication
failures and are not:

* NetEase (126/163) accepts the login and then refuses every ``SELECT`` with
  ``Unsafe Login`` until the client sends the IMAP ``ID`` command. ``imaplib``
  has no verb for it, so it is registered once at import.
* Several Chinese providers archive mail addressed to the account's own address
  in Sent instead of looping it back to the INBOX. A message that is not in the
  inbox is therefore not evidence that the send failed.
"""

from __future__ import annotations

import html
import imaplib
import poplib
import re
import smtplib
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parsedate_to_datetime
from typing import Iterable

from winnie_mail.settings import MailSettings, ReceiveSettings, SmtpSettings

# `imaplib` refuses any verb it does not know, and RFC 2971's ID is not built in.
imaplib.Commands.setdefault("ID", ("NONAUTH", "AUTH", "SELECTED"))

ID_ARGUMENTS = '("name" "winnie" "version" "0.1" "vendor" "winnie")'

TAG_BREAK = re.compile(r"(?is)<(script|style).*?</\1>")
TAG = re.compile(r"(?s)<[^>]+>")
WHITESPACE = re.compile(r"[ \t\r\f\v]+")
BLANK_LINES = re.compile(r"\n{3,}")


class MailError(RuntimeError):
    """A mail operation failed for a reason the owner can act on."""


class MailNotConfigured(MailError):
    """The account is disabled or missing a required field."""


class MailAuthError(MailError):
    """The server rejected the credentials or the client identity."""


class MailTransportError(MailError):
    """The server could not be reached, or answered with an error."""


@dataclass(frozen=True)
class MailMessage:
    """One message as the console shows it: headers plus a short body preview."""

    uid: str
    subject: str
    sender: str
    to: str
    date: str
    snippet: str
    body: str
    unread: bool
    message_id: str


# -- helpers -------------------------------------------------------------


def _header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return str(value).strip()


def _plain_text(message) -> str:
    """The best text part, preferring text/plain over stripped HTML."""
    if message.is_multipart():
        plain = ""
        markup = ""
        for part in message.walk():
            if part.get_content_maintype() != "text":
                continue
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            try:
                body = part.get_content()
            except (LookupError, ValueError):
                continue
            if not isinstance(body, str):
                continue
            if part.get_content_subtype() == "plain" and not plain:
                plain = body
            elif part.get_content_subtype() == "html" and not markup:
                markup = body
        text = plain or markup
    else:
        try:
            text = message.get_content()
        except (LookupError, ValueError):
            text = ""
    if "<" in text and ">" in text:
        text = html.unescape(TAG.sub(" ", TAG_BREAK.sub(" ", text)))
    return text


def _snippet(text: str, *, limit: int = 240) -> str:
    lines = [line.strip() for line in text.splitlines()]
    kept = [line for line in lines if line and not line.startswith(">")]
    joined = " ".join(kept)
    joined = WHITESPACE.sub(" ", joined)
    joined = BLANK_LINES.sub("\n\n", joined).strip()
    if len(joined) <= limit:
        return joined
    return joined[: limit - 1].rstrip() + "…"


def _addresses(value: str | None) -> str:
    # The display name is encoded-word (RFC 2047) often enough that decoding it
    # here, not in the UI, is the difference between a name and `=?utf-8?b?...`.
    pairs = getaddresses([_header(value)])
    rendered = []
    for name, address in pairs:
        if not address:
            continue
        # formataddr would re-encode a decoded Chinese name into another
        # encoded-word, which is what the console must not show.
        rendered.append(
            str(Address(display_name=name, addr_spec=address)) if name else address
        )
    return ", ".join(rendered)


def _date_of(message) -> str:
    raw = (message.get("Date") or "").strip()
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return raw


def _parse(raw: bytes, *, uid: str, unread: bool) -> MailMessage:
    message = message_from_bytes(raw, policy=policy.default)
    text = _plain_text(message)
    return MailMessage(
        uid=uid,
        subject=_header(message.get("Subject")) or "(no subject)",
        sender=_addresses(message.get("From")),
        to=_addresses(message.get("To")),
        date=_date_of(message),
        snippet=_snippet(text),
        body=text.strip()[:4000],
        unread=unread,
        message_id=_header(message.get("Message-ID")),
    )


def _characteristics(settings: MailSettings) -> list[str]:
    problems = settings.problems()
    if not settings.enabled:
        problems = problems or ["mail is disabled"]
    return problems


# -- outgoing ------------------------------------------------------------


def _smtp_connect(config: SmtpSettings, timeout: float) -> smtplib.SMTP:
    try:
        if config.security == "ssl":
            client: smtplib.SMTP = smtplib.SMTP_SSL(config.host, config.port, timeout=timeout)
        else:
            client = smtplib.SMTP(config.host, config.port, timeout=timeout)
            if config.security == "starttls":
                client.starttls()
        return client
    except (OSError, smtplib.SMTPException) as exc:
        raise MailTransportError(f"smtp connect failed: {type(exc).__name__}: {exc}") from exc


def _smtp_login(client: smtplib.SMTP, config: SmtpSettings) -> None:
    if not config.username:
        return
    try:
        client.login(config.username, config.password)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailAuthError(
            f"smtp rejected {config.username}: {exc.smtp_code} {exc.smtp_error!r}"
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise MailTransportError(f"smtp login failed: {type(exc).__name__}: {exc}") from exc


def send(settings: MailSettings, *, to: str | Iterable[str], subject: str, body: str) -> str:
    """Send one plain-text message and return the receipt shown in the log."""
    problems = _characteristics(settings)
    if problems:
        raise MailNotConfigured("; ".join(problems))
    recipients = [to] if isinstance(to, str) else list(to)
    recipients = [address.strip() for value in recipients for address in re.split(r"[,;]", value) if address.strip()]
    if not recipients:
        raise MailNotConfigured("no recipient")

    sender_address = settings.smtp_from()
    sender_name = (settings.smtp.from_name or settings.display_name).strip()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((sender_name, sender_address)) if sender_name else sender_address
    message["To"] = ", ".join(recipients)
    # RFC 5322 requires Date, and filters score a missing one. The header is set
    # here rather than by smtplib, which does not add it.
    message["Date"] = formatdate(localtime=True)
    domain = sender_address.rsplit("@", 1)[-1] if "@" in sender_address else None
    message["Message-ID"] = make_msgid(domain=domain)
    message.set_content(body)

    client = _smtp_connect(settings.smtp, settings.timeout_seconds)
    try:
        _smtp_login(client, settings.smtp)
        client.send_message(message, from_addr=sender_address, to_addrs=recipients)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailTransportError(f"smtp send failed: {type(exc).__name__}: {exc}") from exc
    finally:
        try:
            client.quit()
        except (OSError, smtplib.SMTPException):
            client.close()
    return f"smtp {sender_address} -> {', '.join(recipients)}"


def test_smtp(settings: MailSettings) -> str:
    """Log in without sending, so a wrong password costs nothing."""
    if not settings.smtp.host:
        raise MailNotConfigured("smtp.host is required")
    client = _smtp_connect(settings.smtp, settings.timeout_seconds)
    try:
        client.ehlo()
        _smtp_login(client, settings.smtp)
    finally:
        try:
            client.quit()
        except (OSError, smtplib.SMTPException):
            client.close()
    return f"smtp {settings.smtp.host}:{settings.smtp.port} ok"


# -- incoming ------------------------------------------------------------


def _imap_login(config: ReceiveSettings, settings: MailSettings, timeout: float) -> imaplib.IMAP4:
    username, password = settings.receive_login()
    try:
        if config.security == "ssl":
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(config.host, config.port, timeout=timeout)
        else:
            client = imaplib.IMAP4(config.host, config.port, timeout=timeout)
            if config.security == "starttls":
                client.starttls()
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailTransportError(f"imap connect failed: {type(exc).__name__}: {exc}") from exc
    try:
        client.login(username, password)
    except imaplib.IMAP4.error as exc:
        raise MailAuthError(f"imap rejected {username}: {exc}") from exc
    # NetEase refuses SELECT until ID: the login succeeds and the folder fails,
    # which reads like a permissions problem. Send it when the server offers it.
    capabilities = {
        value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
        for value in (client.capabilities or ())
    }
    if "ID" in capabilities:
        try:
            client._simple_command("ID", ID_ARGUMENTS)  # noqa: SLF001 - stdlib has no ID verb
        except imaplib.IMAP4.error as exc:
            raise MailAuthError(f"imap rejected the client identity: {exc}") from exc
    return client


def _imap_fetch(settings: MailSettings, limit: int) -> list[MailMessage]:
    config = settings.receive
    client = _imap_login(config, settings, settings.timeout_seconds)
    try:
        folder = config.folder or "INBOX"
        quoted = f'"{folder}"' if " " in folder else folder
        try:
            status, data = client.select(quoted, readonly=True)
        except imaplib.IMAP4.error as exc:
            raise MailTransportError(f"imap select {folder} failed: {exc}") from exc
        if status != "OK":
            raise MailTransportError(f"imap select {folder} failed: {data!r}")
        try:
            status, data = client.search(None, "ALL")
        except imaplib.IMAP4.error as exc:
            raise MailTransportError(f"imap search failed: {exc}") from exc
        if status != "OK":
            raise MailTransportError(f"imap search failed: {data!r}")
        ids = data[0].split()
        messages: list[MailMessage] = []
        for identifier in reversed(ids[-limit:]):
            status, parts = client.fetch(identifier, "(FLAGS BODY.PEEK[])")
            if status != "OK":
                continue
            raw = b""
            flags = b""
            for part in parts:
                if isinstance(part, tuple):
                    flags += part[0]
                    raw = part[1]
            uid = identifier.decode("ascii", "replace")
            messages.append(
                _parse(raw, uid=uid, unread=b"\\Seen" not in flags)
            )
        return messages
    finally:
        try:
            client.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


def _pop3_login(config: ReceiveSettings, settings: MailSettings, timeout: float):
    username, password = settings.receive_login()
    try:
        if config.security == "ssl":
            client = poplib.POP3_SSL(config.host, config.port, timeout=timeout)
        else:
            client = poplib.POP3(config.host, config.port, timeout=timeout)
            if config.security == "starttls":
                client.stls()
    except (OSError, poplib.error_proto) as exc:
        raise MailTransportError(f"pop3 connect failed: {type(exc).__name__}: {exc}") from exc
    try:
        client.user(username)
        client.pass_(password)
    except poplib.error_proto as exc:
        raise MailAuthError(f"pop3 rejected {username}: {exc}") from exc
    return client


def _pop3_fetch(settings: MailSettings, limit: int) -> list[MailMessage]:
    config = settings.receive
    client = _pop3_login(config, settings, settings.timeout_seconds)
    try:
        try:
            count, _size = client.stat()
        except poplib.error_proto as exc:
            raise MailTransportError(f"pop3 stat failed: {exc}") from exc
        messages: list[MailMessage] = []
        for index in range(count, max(0, count - limit), -1):
            try:
                _response, lines, _octets = client.retr(index)
            except poplib.error_proto:
                continue
            try:
                uid = client.uidl(index).decode("ascii", "replace").split()[-1]
            except (poplib.error_proto, IndexError, AttributeError):
                uid = str(index)
            messages.append(_parse(b"\r\n".join(lines), uid=uid, unread=False))
        return messages
    finally:
        try:
            client.quit()
        except (OSError, poplib.error_proto):
            pass


def fetch_recent(settings: MailSettings, *, limit: int = 20) -> list[MailMessage]:
    """The newest messages, without marking anything read."""
    if not settings.receive.protocol or settings.receive.protocol == "none":
        raise MailNotConfigured("receiving is not enabled")
    if not settings.receive.host:
        raise MailNotConfigured("receive.host is required")
    limit = max(1, min(limit, settings.max_messages, 200))
    if settings.receive.protocol == "imap":
        return _imap_fetch(settings, limit)
    return _pop3_fetch(settings, limit)


def test_receive(settings: MailSettings) -> str:
    """Log in and count the mailbox, without downloading a message."""
    if not settings.receive.protocol or settings.receive.protocol == "none":
        raise MailNotConfigured("receiving is not enabled")
    if not settings.receive.host:
        raise MailNotConfigured("receive.host is required")
    if settings.receive.protocol == "imap":
        client = _imap_login(settings.receive, settings, settings.timeout_seconds)
        try:
            status, data = client.select(settings.receive.folder or "INBOX", readonly=True)
            if status != "OK":
                raise MailTransportError(f"imap select failed: {data!r}")
            return (
                f"imap {settings.receive.host}:{settings.receive.port} "
                f"{data[0].decode('ascii', 'replace') if data else '0'} messages"
            )
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
    client = _pop3_login(settings.receive, settings, settings.timeout_seconds)
    try:
        count, size = client.stat()
        return f"pop3 {settings.receive.host}:{settings.receive.port} {count} messages, {size} bytes"
    finally:
        try:
            client.quit()
        except (OSError, poplib.error_proto):
            pass
