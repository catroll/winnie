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

import base64
import binascii
import html
import imaplib
import poplib
import re
import smtplib
from dataclasses import dataclass, field
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


class MailUnavailable(MailError):
    """The cache has no database, so browsing is not possible on this host."""


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


@dataclass(frozen=True)
class FolderInfo:
    """One mailbox folder. ``name`` is the server's spelling, not the label."""

    name: str
    display_name: str
    delimiter: str = ""
    selectable: bool = True


@dataclass(frozen=True)
class HeaderMessage:
    """Headers only: what a sync stores before anyone opens the message."""

    uid: str
    subject: str
    sender: str
    to: str
    date: str
    message_id: str
    unread: bool
    size: int


@dataclass
class FolderSnapshot:
    """One folder's answer to "what changed since the cursor".

    ``fetched`` are new messages, ``flags`` is the unread state of messages
    already cached, and ``removed`` lists uids the server no longer has. A
    changed ``uid_validity`` sets ``reset``: every cached uid in that folder is
    meaningless and must be dropped before applying the snapshot.
    """

    folder: FolderInfo
    uid_validity: int = 0
    total: int = 0
    unread: int = 0
    fetched: list[HeaderMessage] = field(default_factory=list)
    flags: dict[str, bool] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)
    reset: bool = False
    high_uid: int = 0


@dataclass(frozen=True)
class FolderPlan:
    """One folder to visit, with the cursor the last sync left behind."""

    folder: FolderInfo
    known_uids: frozenset[str] = frozenset()
    last_uid: int = 0
    uid_validity: int = 0


@dataclass
class FolderResult:
    """Either a folder's snapshot or the reason it could not be read.

    One broken folder must not abandon the sweep: a folder the server refuses is
    recorded, the rest are still synced, and the next pass retries it.
    """

    folder: FolderInfo
    snapshot: FolderSnapshot | None = None
    error: str = ""


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


def _parse(raw: bytes, *, uid: str, unread: bool, body_limit: int = 4000) -> MailMessage:
    message = message_from_bytes(raw, policy=policy.default)
    text = _plain_text(message)
    return MailMessage(
        uid=uid,
        subject=_header(message.get("Subject")) or "(no subject)",
        sender=_addresses(message.get("From")),
        to=_addresses(message.get("To")),
        date=_date_of(message),
        snippet=_snippet(text),
        body=text.strip()[:body_limit],
        unread=unread,
        message_id=_header(message.get("Message-ID")),
    )


def _parse_headers(raw: bytes, *, uid: str, unread: bool, size: int) -> HeaderMessage:
    message = message_from_bytes(raw, policy=policy.default)
    return HeaderMessage(
        uid=uid,
        subject=_header(message.get("Subject")) or "(no subject)",
        sender=_addresses(message.get("From")),
        to=_addresses(message.get("To")),
        date=_date_of(message),
        message_id=_header(message.get("Message-ID")),
        unread=unread,
        size=size,
    )


def _decode_modified_utf7(value: str) -> str:
    """RFC 3501 modified UTF-7, e.g. ``&XfJT0ZAB-`` is the senders' folder.

    IMAP spells non-ASCII folder names in this variant of UTF-7; the standard
    library has no decoder, and showing ``&XfJT0ZAB-`` in a folder list is what
    makes an IMAP client look broken.
    """
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "&":
            out.append(char)
            index += 1
            continue
        end = value.find("-", index)
        if end == -1:
            out.append(value[index:])
            break
        block = value[index + 1 : end]
        if not block:
            out.append("&")
        else:
            data = block.replace(",", "/")
            try:
                out.append(
                    base64.b64decode(data + "=" * (-len(data) % 4)).decode("utf-16-be")
                )
            except (binascii.Error, UnicodeDecodeError):
                out.append(value[index : end + 1])
        index = end + 1
    return "".join(out)


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


def _imap_select(client: imaplib.IMAP4, folder: str) -> tuple[int, int]:
    """Select one folder read-only. Returns the uid validity and the count."""
    quoted = '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'
    try:
        status, data = client.select(quoted, readonly=True)
    except imaplib.IMAP4.error as exc:
        raise MailTransportError(f"imap select {folder} failed: {exc}") from exc
    if status != "OK":
        raise MailTransportError(f"imap select {folder} failed: {data!r}")
    total = int(data[0]) if data and data[0] else 0
    validity = 0
    try:
        kind, values = client.response("UIDVALIDITY")
    except imaplib.IMAP4.error:
        kind, values = None, None
    if kind and values and values[0]:
        try:
            validity = int(values[0])
        except (TypeError, ValueError):
            validity = 0
    return validity, total


def _imap_open(settings: MailSettings, folder: str) -> tuple[imaplib.IMAP4, int, int]:
    """Log in and select one folder read-only. Returns the client, validity, count."""
    client = _imap_login(settings.receive, settings, settings.timeout_seconds)
    try:
        validity, total = _imap_select(client, folder)
    except MailError:
        try:
            client.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
        raise
    return client, validity, total


def _imap_search_uids(client: imaplib.IMAP4) -> list[int]:
    status, data = client.uid("SEARCH", None, "ALL")
    if status != "OK":
        raise MailTransportError(f"imap search failed: {data!r}")
    raw = b" ".join(value for value in data or [] if isinstance(value, (bytes, bytearray)))
    return [int(value) for value in raw.split() if value.isdigit()]


def _fetch_units(parts) -> list[tuple[str, bytes, bytes, int]]:
    """Split a batched FETCH answer into (uid, flags, raw, size) tuples.

    The first number on a FETCH line is the *sequence number*, not the uid: only
    ``UID`` in the requested items makes the server echo the real one. Asking for
    it explicitly is the difference between a correct cursor and one that stores
    1..N and then treats every message as both new and deleted on the next sync.
    """
    units = []
    for part in parts or []:
        if not isinstance(part, tuple) or len(part) < 2:
            continue
        prefix, raw = part[0], part[1]
        if not isinstance(prefix, (bytes, bytearray)):
            continue
        match = re.search(rb"\bUID (\d+)", prefix)
        if not match:
            raise MailTransportError(
                f"imap fetch answer has no UID: {bytes(prefix)[:120]!r}"
            )
        flags = re.search(rb"FLAGS \(([^)]*)\)", prefix)
        size = re.search(rb"RFC822\.SIZE (\d+)", prefix)
        units.append(
            (
                match.group(1).decode("ascii", "replace"),
                flags.group(1) if flags else b"",
                raw if isinstance(raw, (bytes, bytearray)) else b"",
                int(size.group(1)) if size else 0,
            )
        )
    return units


def _imap_headers(client: imaplib.IMAP4, uids: list[int]) -> list[HeaderMessage]:
    """Fetch headers only, in batches of 20 uids (one round trip per batch)."""
    messages: list[HeaderMessage] = []
    for start in range(0, len(uids), 20):
        batch = uids[start : start + 20]
        status, parts = client.uid(
            "FETCH",
            ",".join(str(uid) for uid in batch),
            "(UID FLAGS BODY.PEEK[HEADER] RFC822.SIZE)",
        )
        if status != "OK":
            raise MailTransportError(f"imap fetch failed: {parts!r}")
        for uid, flags, raw, size in _fetch_units(parts):
            messages.append(
                _parse_headers(
                    raw, uid=uid, unread=b"\\Seen" not in flags, size=size
                )
            )
    return messages


def _imap_flags(client: imaplib.IMAP4, uids: list[int]) -> dict[str, bool]:
    """Refresh the read state of messages already cached, without headers."""
    flags: dict[str, bool] = {}
    for start in range(0, len(uids), 20):
        batch = uids[start : start + 20]
        status, parts = client.uid(
            "FETCH", ",".join(str(uid) for uid in batch), "(UID FLAGS)"
        )
        if status != "OK":
            continue
        for part in parts or []:
            if not isinstance(part, (bytes, bytearray)):
                continue
            prefix = bytes(part)
            match = re.search(rb"\bUID (\d+)", prefix)
            value = re.search(rb"FLAGS \(([^)]*)\)", prefix)
            if not match:
                continue
            flags[match.group(1).decode("ascii", "replace")] = (
                b"\\Seen" not in (value.group(1) if value else b"")
            )
    return flags


def _list_entry(entry: object) -> FolderInfo | None:
    if not isinstance(entry, (bytes, bytearray)):
        return None
    text = bytes(entry).decode("utf-8", "replace")
    match = re.match(
        r'\((?P<flags>[^)]*)\)\s+(?:"(?P<delimiter>[^"]*)"|NIL)\s+(?P<name>.+)$',
        text,
        re.S,
    )
    if not match:
        return None
    name = match.group("name").strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('\\"', '"')
    elif name.startswith("{"):
        # A literal mailbox name; take what follows the literal marker.
        _, _, name = name.partition("}")
    if not name:
        return None
    flags = match.group("flags") or ""
    return FolderInfo(
        name=name,
        display_name=_decode_modified_utf7(name),
        delimiter=match.group("delimiter") or "",
        selectable="\\Noselect" not in flags and "\\NonExistent" not in flags,
    )


def _check_receive(settings: MailSettings, *, folder: str = "") -> None:
    if not settings.receive.protocol or settings.receive.protocol == "none":
        raise MailNotConfigured("receiving is not enabled")
    if not settings.receive.host:
        raise MailNotConfigured("receive.host is required")


def list_folders(settings: MailSettings) -> list[FolderInfo]:
    """Every folder the account has, not only the one the form names."""
    _check_receive(settings)
    if settings.receive.protocol == "pop3":
        return [FolderInfo(name="INBOX", display_name="INBOX")]
    client = _imap_login(settings.receive, settings, settings.timeout_seconds)
    try:
        try:
            status, data = client.list()
        except imaplib.IMAP4.error as exc:
            raise MailTransportError(f"imap list failed: {exc}") from exc
        if status != "OK":
            raise MailTransportError(f"imap list failed: {data!r}")
        folders = [info for info in (_list_entry(entry) for entry in data or []) if info]
        # INBOX first, then the server's own order; a stable list keeps the
        # console from reordering itself between syncs.
        folders.sort(key=lambda info: (info.name.upper() != "INBOX", info.name))
        return folders
    finally:
        try:
            client.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


def _imap_snapshot(
    client: imaplib.IMAP4,
    folder: FolderInfo,
    *,
    known_uids: set[str],
    last_uid: int,
    uid_validity: int,
    limit: int,
) -> FolderSnapshot:
    """What changed in one folder, over an already selected-able session."""
    validity, total = _imap_select(client, folder.name)
    reset = bool(uid_validity) and bool(validity) and validity != uid_validity
    all_uids = _imap_search_uids(client)
    known = set() if reset else set(known_uids)
    window = all_uids[-limit:]
    known_numbers = {int(uid) for uid in known if uid.isdigit()}
    to_fetch = [uid for uid in window if uid not in known_numbers]
    cached_window = [uid for uid in window if uid in known_numbers]
    removed = sorted(
        uid for uid in known if uid.isdigit() and int(uid) not in set(all_uids)
    )
    return FolderSnapshot(
        folder=folder,
        uid_validity=validity,
        total=total,
        unread=0,
        fetched=_imap_headers(client, to_fetch),
        flags=_imap_flags(client, cached_window),
        removed=removed,
        reset=reset,
        high_uid=all_uids[-1] if all_uids else last_uid,
    )


def _pop3_login(config: ReceiveSettings, settings: MailSettings, timeout: float):
    """Log in to POP3. The server has one mailbox; the folder name is ignored."""
    username, password = settings.receive_login()
    try:
        if config.security == "ssl":
            client = poplib.POP3_SSL(config.host, config.port, timeout=timeout)
        else:
            client = poplib.POP3(config.host, config.port, timeout=timeout)
            if config.security == "starttls":
                client.stls()
    except (OSError, poplib.error_proto) as exc:
        raise MailTransportError(
            f"pop3 connect failed: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        client.user(username)
        client.pass_(password)
    except poplib.error_proto as exc:
        raise MailAuthError(f"pop3 rejected {username}: {exc}") from exc
    return client


def _pop3_listing(client) -> list[tuple[int, str]]:
    try:
        _status, lines, _octets = client.uidl()
    except poplib.error_proto as exc:
        raise MailTransportError(f"pop3 uidl failed: {exc}") from exc
    pairs = []
    for line in lines:
        parts = line.decode("ascii", "replace").split()
        if len(parts) >= 2 and parts[0].isdigit():
            pairs.append((int(parts[0]), parts[1]))
    return pairs


def _pop3_snapshot(
    client, folder: FolderInfo, *, known_uids: set[str], limit: int
) -> FolderSnapshot:
    """What one POP3 mailbox gained since the cached uids, headers only.

    POP3 has no per-folder view and no stable numeric ids, so the cursor is the
    set of UIDLs already cached: anything absent from ``UIDL`` has been deleted
    on the server, anything present that we have not seen is new.
    """
    pairs = _pop3_listing(client)
    present = {uid for _, uid in pairs}
    removed = sorted(known_uids - present)
    fresh = [(index, uid) for index, uid in pairs if uid not in known_uids][-limit:]
    fetched: list[HeaderMessage] = []
    for index, uid in fresh:
        try:
            _status, lines, _octets = client.top(index, 0)
            raw = b"\r\n".join(lines)
        except poplib.error_proto:
            # TOP is optional in RFC 1939; a server without it costs a full
            # RETR for the header, which is the best this protocol offers.
            _status, lines, _octets = client.retr(index)
            raw = b"\r\n".join(lines)
        fetched.append(_parse_headers(raw, uid=uid, unread=False, size=len(raw)))
    return FolderSnapshot(
        folder=folder,
        total=len(pairs),
        fetched=fetched,
        removed=removed,
        high_uid=len(pairs),
    )


def sync_folder(
    settings: MailSettings,
    folder: FolderInfo,
    *,
    known_uids: set[str] | None = None,
    last_uid: int = 0,
    uid_validity: int = 0,
    limit: int = 50,
) -> FolderSnapshot:
    """What changed in one folder since the cursor. Headers only, no bodies."""
    _check_receive(settings)
    if not folder.selectable:
        return FolderSnapshot(folder=folder, total=0, high_uid=last_uid)
    limit = max(1, min(limit, 200))
    if settings.receive.protocol == "imap":
        client = _imap_login(settings.receive, settings, settings.timeout_seconds)
        try:
            return _imap_snapshot(
                client,
                folder,
                known_uids=known_uids or set(),
                last_uid=last_uid,
                uid_validity=uid_validity,
                limit=limit,
            )
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
    client = _pop3_login(settings.receive, settings, settings.timeout_seconds)
    try:
        return _pop3_snapshot(
            client, folder, known_uids=known_uids or set(), limit=limit
        )
    finally:
        try:
            client.quit()
        except (OSError, poplib.error_proto):
            pass


def sync_all(
    settings: MailSettings, plans: list[FolderPlan], *, limit: int = 50
) -> list[FolderResult]:
    """Walk every folder over one connection, newest ``limit`` messages each.

    One login instead of one per folder matters: a mailbox with two dozen folders
    otherwise opens two dozen TLS sessions in a row, which is slow and looks like
    abuse to the provider (126 refuses further connections after a burst).
    """
    _check_receive(settings)
    limit = max(1, min(limit, 200))
    results: list[FolderResult] = []
    if settings.receive.protocol == "imap":
        client = _imap_login(settings.receive, settings, settings.timeout_seconds)
        try:
            for plan in plans:
                if not plan.folder.selectable:
                    results.append(FolderResult(plan.folder, FolderSnapshot(folder=plan.folder)))
                    continue
                try:
                    results.append(
                        FolderResult(
                            plan.folder,
                            _imap_snapshot(
                                client,
                                plan.folder,
                                known_uids=set(plan.known_uids),
                                last_uid=plan.last_uid,
                                uid_validity=plan.uid_validity,
                                limit=limit,
                            ),
                        )
                    )
                except MailError as exc:
                    results.append(FolderResult(plan.folder, None, str(exc)))
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
        return results
    client = _pop3_login(settings.receive, settings, settings.timeout_seconds)
    try:
        for plan in plans:
            try:
                results.append(
                    FolderResult(
                        plan.folder,
                        _pop3_snapshot(
                            client,
                            plan.folder,
                            known_uids=set(plan.known_uids),
                            limit=limit,
                        ),
                    )
                )
            except MailError as exc:
                results.append(FolderResult(plan.folder, None, str(exc)))
    finally:
        try:
            client.quit()
        except (OSError, poplib.error_proto):
            pass
    return results


def fetch_body(settings: MailSettings, *, folder: str, uid: str) -> MailMessage:
    """Download one message and return it with its body, for the reading pane."""
    _check_receive(settings)
    folder = folder or (settings.receive.folder or "INBOX")
    if settings.receive.protocol == "imap":
        client, _validity, _total = _imap_open(settings, folder)
        try:
            status, parts = client.uid("FETCH", uid, "(UID FLAGS BODY.PEEK[])")
            if status != "OK":
                raise MailTransportError(f"imap fetch {uid} failed: {parts!r}")
            units = _fetch_units(parts)
            if not units:
                raise MailTransportError(f"imap returned no message for uid {uid}")
            unit_uid, flags, raw, _size = units[0]
            return _parse(
                raw,
                uid=unit_uid,
                unread=b"\\Seen" not in flags,
                body_limit=20000,
            )
        except imaplib.IMAP4.error as exc:
            raise MailTransportError(f"imap fetch {uid} failed: {exc}") from exc
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
    client = _pop3_login(settings.receive, settings, settings.timeout_seconds)
    try:
        index = next(
            (number for number, value in _pop3_listing(client) if value == uid), None
        )
        if index is None:
            raise MailTransportError(f"pop3 no longer has message {uid}")
        _status, lines, _octets = client.retr(index)
        return _parse(b"\r\n".join(lines), uid=uid, unread=False, body_limit=20000)
    except poplib.error_proto as exc:
        raise MailTransportError(f"pop3 fetch {uid} failed: {exc}") from exc
    finally:
        try:
            client.quit()
        except (OSError, poplib.error_proto):
            pass


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
