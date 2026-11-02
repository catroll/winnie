"""The mailbox: settings, the three protocols, the cache and the HTTP surface.

The protocol clients are exercised through fakes of ``smtplib``/``imaplib``/
``poplib`` rather than a live server: what matters here is what we send, what we
store, and the two provider quirks that once looked like a bad password. A
live-account check is a manual step, documented in docs/mail.md.
"""

from __future__ import annotations

import stat
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from winnie_api.main import create_app
from winnie_api.settings import DatabaseConfig, Settings
from winnie_core.config import AuthConfig, JobsConfig
from winnie_core.db import create_all, create_engine, create_session_factory
from winnie_mail import client
from winnie_mail.cache import MailCache
from winnie_mail.config import MailConfig
from winnie_mail.schemas import MailUpdate
from winnie_mail.service import MailService
from winnie_mail.settings import MailSettings, MailSettingsStore
from winnie_meetings.config import MeetingsConfig

OWNER = "owner-1"


def a_settings(root: Path, **overrides) -> MailSettings:
    values = dict(
        enabled=True,
        address="winnie@example.com",
        display_name="Winnie",
        smtp={
            "host": "smtp.example.com",
            "port": 465,
            "security": "ssl",
            "username": "winnie@example.com",
            "password": "s3cret",
            "from_name": "Winnie",
        },
        receive={
            "protocol": "imap",
            "host": "imap.example.com",
            "port": 993,
            "security": "ssl",
        },
    )
    values.update(overrides)
    return MailSettings.model_validate(values)


# -- storage -------------------------------------------------------------


def test_the_settings_file_is_private_and_round_trips(tmp_path: Path) -> None:
    store = MailSettingsStore(tmp_path / "mail")
    saved = store.save(OWNER, a_settings(tmp_path))

    path = store.path(OWNER)
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.load(OWNER).smtp.password == "s3cret"
    assert store.load(OWNER).address == "winnie@example.com"
    assert saved.updated_at


def test_an_unknown_account_loads_defaults(tmp_path: Path) -> None:
    store = MailSettingsStore(tmp_path / "mail")
    settings = store.load("nobody")
    assert settings.enabled is False
    assert settings.can_send() is False


def test_a_user_id_cannot_name_a_path(tmp_path: Path) -> None:
    store = MailSettingsStore(tmp_path / "mail")
    for unsafe in ("../escape", "a/b", "", "x" * 200):
        with pytest.raises(ValueError):
            store.path(unsafe)


def test_the_store_says_whether_any_account_exists(tmp_path: Path) -> None:
    store = MailSettingsStore(tmp_path / "mail")
    assert store.any_account() is False
    store.save(OWNER, a_settings(tmp_path))
    assert store.any_account() is True


def test_clear_removes_the_file(tmp_path: Path) -> None:
    store = MailSettingsStore(tmp_path / "mail")
    store.save(OWNER, a_settings(tmp_path))
    assert store.clear(OWNER) is True
    assert store.clear(OWNER) is False


def test_saving_without_a_password_keeps_the_stored_one(tmp_path: Path) -> None:
    service = MailService(MailSettingsStore(tmp_path / "mail"))
    service.apply(OWNER, MailUpdate.model_validate(a_payload()))
    view = service.apply(
        OWNER,
        MailUpdate.model_validate(a_payload(smtp_password=None)),
    )
    assert view.smtp.password_set is True
    assert service.load(OWNER).smtp.password == "s3cret"


def test_an_empty_password_clears_it(tmp_path: Path) -> None:
    service = MailService(MailSettingsStore(tmp_path / "mail"))
    service.apply(OWNER, MailUpdate.model_validate(a_payload()))
    view = service.apply(OWNER, MailUpdate.model_validate(a_payload(smtp_password="")))
    assert view.smtp.password_set is False


def a_payload(*, smtp_password="s3cret") -> dict:
    return {
        "enabled": True,
        "address": "winnie@example.com",
        "display_name": "Winnie",
        "smtp": {
            "host": "smtp.example.com",
            "port": 465,
            "security": "ssl",
            "username": "winnie@example.com",
            "password": smtp_password,
        },
        "receive": {
            "protocol": "imap",
            "host": "imap.example.com",
            "port": 993,
            "security": "ssl",
            "use_send_credentials": True,
        },
    }


# -- outgoing ------------------------------------------------------------


class FakeSmtp:
    instances: list["FakeSmtp"] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.user = ""
        self.password = ""
        self.message = None
        self.from_addr = ""
        self.to_addrs: list[str] = []
        FakeSmtp.instances.append(self)

    def ehlo(self) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        self.user = user
        self.password = password

    def send_message(self, message, from_addr=None, to_addrs=None) -> None:
        self.message = message
        self.from_addr = from_addr
        self.to_addrs = list(to_addrs or [])

    def quit(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture
def fake_smtp(monkeypatch) -> type[FakeSmtp]:
    FakeSmtp.instances = []
    monkeypatch.setattr(client.smtplib, "SMTP_SSL", FakeSmtp)
    return FakeSmtp


def test_send_sets_the_headers_a_filter_looks_for(fake_smtp, tmp_path: Path) -> None:
    settings = a_settings(tmp_path)
    receipt = client.send(settings, to="recipient@example.com", subject="你好", body="body")

    sent = fake_smtp.instances[-1]
    assert sent.user == "winnie@example.com"
    assert sent.password == "s3cret"
    assert sent.from_addr == "winnie@example.com"
    assert sent.to_addrs == ["recipient@example.com"]
    assert sent.message["Date"]
    assert sent.message["Message-ID"].endswith("@example.com>")
    assert sent.message["Subject"] == "你好"
    assert "smtp winnie@example.com" in receipt


def test_send_splits_several_recipients(fake_smtp, tmp_path: Path) -> None:
    client.send(a_settings(tmp_path), to="a@example.com, b@example.com", subject="s", body="b")
    assert fake_smtp.instances[-1].to_addrs == ["a@example.com", "b@example.com"]


def test_a_disabled_account_refuses_to_send(tmp_path: Path) -> None:
    settings = a_settings(tmp_path, enabled=False)
    with pytest.raises(client.MailNotConfigured):
        client.send(settings, to="a@example.com", subject="s", body="b")


def test_a_rejected_login_is_an_auth_error(monkeypatch, tmp_path: Path) -> None:
    class Rejecting(FakeSmtp):
        def login(self, user: str, password: str) -> None:
            import smtplib

            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(client.smtplib, "SMTP_SSL", Rejecting)
    with pytest.raises(client.MailAuthError):
        client.test_smtp(a_settings(tmp_path))


# -- incoming ------------------------------------------------------------

HEADERS = (
    "From: =?utf-8?b?572R5piT6YKu566x6LSm5Y+35a6J5YWo?= <safe@service.example.com>\r\n"
    "To: winnie@example.com\r\n"
    "Subject: =?utf-8?b?5paw6K6+5aSH55m75b2V5o+Q6YaS?=\r\n"
    "Date: Mon, 2 Nov 2026 22:00:08 +0800\r\n"
    "Message-ID: <abc@example.com>\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "Content-Transfer-Encoding: 8bit\r\n"
)
RAW = (HEADERS + "\r\n您的邮箱刚刚在新设备上登录，如非本人请修改密码。\r\n").encode("utf-8")
RAW_SECOND = (
    HEADERS.replace("abc@example.com", "def@example.com")
    .replace("5paw6K6+5aSH55m75b2V5o+Q6YaS", "5L2g5aW95ZWK")
    + "\r\n第二条消息的正文。\r\n"
).encode("utf-8")


class FakeImap:
    """A server-side mailbox, shared across connections like a real one.

    Every ``list_folders``/``sync_folder`` call opens its own connection, so the
    mailboxes are class state: appending a message must be visible to the next
    connection, exactly as it would be on the server.
    """

    instances: list["FakeImap"] = []
    mailboxes: dict[str, dict[int, bytes]] | None = None
    seen_flags: dict[str, dict[int, bytes]] | None = None
    fetch_calls: list[tuple[str, str]] = []
    uid_validity_value: int = 42

    @classmethod
    def reset(cls) -> None:
        """Start a test from a known mailbox that every connection shares."""
        cls.instances = []
        cls.fetch_calls = []
        cls.uid_validity_value = 42
        # Real uids are large and unrelated to the sequence numbers a server
        # puts first on a FETCH line; start high so a mix-up cannot pass.
        cls.mailboxes = {
            "INBOX": {101: RAW, 102: RAW_SECOND},
            "Sent": {105: RAW},
            "&XfJT0ZAB-": {},
            "Parent": {},
        }
        cls.seen_flags = {
            "INBOX": {101: b"\\Seen", 102: b""},
            "Sent": {105: b"\\Seen"},
            "&XfJT0ZAB-": {},
            "Parent": {},
        }

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        if FakeImap.mailboxes is None:
            FakeImap.reset()
        self.host = host
        self.port = port
        self.capabilities = ("IMAP4rev1", "ID")
        self.sent_identity = False
        self.uid_validity = FakeImap.uid_validity_value
        self.folder = ""
        self.boxes = FakeImap.mailboxes
        self.flags = FakeImap.seen_flags
        FakeImap.fetch_calls = self.fetch_calls = []
        FakeImap.instances.append(self)

    # -- session ---------------------------------------------------------

    def login(self, user: str, password: str) -> None:
        assert (user, password) == ("winnie@example.com", "s3cret")

    def _simple_command(self, name, *args):
        assert name == "ID"
        self.sent_identity = True
        return "OK", [b"ID completed"]

    def list(self, directory='""', pattern="*"):
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Sent"',
            b'(\\HasNoChildren) "/" "&XfJT0ZAB-"',
            b'(\\Noselect) "/" "Parent"',
        ]

    def select(self, folder, readonly=False):
        name = folder.strip('"')
        if name not in self.boxes:
            return "NO", [b"no such mailbox"]
        assert readonly is True
        assert self.sent_identity, "126 refuses SELECT until the ID command is sent"
        self.folder = name
        return "OK", [str(len(self.boxes[name])).encode()]

    def response(self, name):
        assert name == "UIDVALIDITY"
        return "UIDVALIDITY", [str(self.uid_validity).encode()]

    def uid(self, command, *args):
        assert self.folder, "select a folder first"
        box = self.boxes[self.folder]
        if command == "SEARCH":
            return "OK", [b" ".join(str(uid).encode() for uid in sorted(box))]
        if command == "FETCH":
            uids = [int(value) for value in str(args[0]).split(",")]
            spec = str(args[1])
            self.fetch_calls.append((str(args[0]), spec))
            parts: list = []
            order = sorted(box)
            for uid in uids:
                if uid not in box:
                    continue
                # A real server prefixes the answer with the sequence number and
                # only echoes the uid when it was asked for.
                sequence = order.index(uid) + 1
                uid_field = f" UID {uid}" if "UID" in spec else ""
                flags = f" FLAGS ({self.flags[self.folder].get(uid, b'').decode()})"
                raw = box[uid]
                if "BODY.PEEK[HEADER]" in spec:
                    header = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                    prefix = (
                        f"{sequence} ({uid_field}{flags} BODY[HEADER] "
                        f"{{{len(header)}}}"
                    ).encode()
                    parts.extend([(prefix, header), b")"])
                elif "BODY.PEEK[]" in spec:
                    prefix = (
                        f"{sequence} ({uid_field}{flags} BODY[] {{{len(raw)}}}"
                    ).encode()
                    parts.extend([(prefix, raw), b")"])
                else:
                    parts.append(f"{sequence} ({uid_field}{flags})".encode())
            return "OK", parts
        raise AssertionError(f"unexpected UID command {command}")

    def logout(self) -> None:
        return None


@pytest.fixture
def fake_imap(monkeypatch) -> type[FakeImap]:
    FakeImap.reset()
    monkeypatch.setattr(client.imaplib, "IMAP4_SSL", FakeImap)
    return FakeImap


def test_list_folders_decodes_the_names_and_marks_unselectable(fake_imap, tmp_path: Path) -> None:
    folders = client.list_folders(a_settings(tmp_path))
    names = [folder.name for folder in folders]
    assert names == ["INBOX", "&XfJT0ZAB-", "Parent", "Sent"]
    encoded = next(folder for folder in folders if folder.name == "&XfJT0ZAB-")
    # RFC 3501 modified UTF-7: this is the Chinese "Sent" folder.
    assert encoded.display_name == "已发送"
    parent = next(folder for folder in folders if folder.name == "Parent")
    assert parent.selectable is False


def test_a_sync_fetches_headers_and_advances_the_cursor(fake_imap, tmp_path: Path) -> None:
    snapshot = client.sync_folder(
        a_settings(tmp_path),
        client.FolderInfo(name="INBOX", display_name="INBOX"),
        known_uids=set(),
        limit=10,
    )
    assert [message.uid for message in snapshot.fetched] == ["101", "102"]
    assert snapshot.uid_validity == 42
    assert snapshot.high_uid == 102
    # Headers only: the body is not in the response the sync asked for.
    assert all(
        "BODY.PEEK[HEADER]" in spec for _uids, spec in FakeImap.fetch_calls
    )
    decoded = snapshot.fetched[0]
    assert decoded.subject == "新设备登录提醒"
    assert decoded.sender == "网易邮箱账号安全 <safe@service.example.com>"
    assert decoded.unread is False
    assert snapshot.fetched[1].unread is True


def test_the_second_sync_asks_only_for_the_new_uid(fake_imap, tmp_path: Path) -> None:
    settings = a_settings(tmp_path)
    folder = client.FolderInfo(name="INBOX", display_name="INBOX")
    client.sync_folder(settings, folder, known_uids=set(), limit=10)

    # A new message arrives on the server between the two syncs.
    fake_imap.mailboxes["INBOX"][103] = RAW
    fake_imap.seen_flags["INBOX"][103] = b""
    FakeImap.fetch_calls.clear()
    snapshot = client.sync_folder(
        settings,
        folder,
        known_uids={"101", "102"},
        last_uid=102,
        uid_validity=42,
        limit=10,
    )
    assert [message.uid for message in snapshot.fetched] == ["103"]
    header_fetches = [
        uids for uids, spec in FakeImap.fetch_calls if "HEADER" in spec
    ]
    assert header_fetches == ["103"]
    assert snapshot.flags == {"101": False, "102": True}


def test_a_deleted_message_leaves_the_cache(fake_imap, tmp_path: Path) -> None:
    settings = a_settings(tmp_path)
    folder = client.FolderInfo(name="INBOX", display_name="INBOX")
    snapshot = client.sync_folder(
        settings, folder, known_uids={"101", "999"}, last_uid=102, uid_validity=42
    )
    assert snapshot.removed == ["999"]
    # uid 101 is already cached, so only the header of 102 is downloaded; 101
    # stays in the cache and its read state arrives through ``flags``.
    assert [message.uid for message in snapshot.fetched] == ["102"]
    assert snapshot.flags == {"101": False}


def test_a_new_uidvalidity_resets_the_folder(fake_imap, tmp_path: Path) -> None:
    # UIDVALIDITY changes when the server rebuilds the mailbox: every cached
    # uid is meaningless, so the folder must be refetched from scratch.
    fake_imap.uid_validity_value = 99
    settings = a_settings(tmp_path)
    folder = client.FolderInfo(name="INBOX", display_name="INBOX")
    snapshot = client.sync_folder(
        settings,
        folder,
        known_uids={"101", "102"},
        last_uid=102,
        uid_validity=42,
    )
    assert snapshot.reset is True
    assert snapshot.removed == []
    assert [message.uid for message in snapshot.fetched] == ["101", "102"]


def test_one_connection_walks_every_folder(fake_imap, tmp_path: Path) -> None:
    plans = [
        client.FolderPlan(client.FolderInfo(name="INBOX", display_name="INBOX")),
        client.FolderPlan(client.FolderInfo(name="Sent", display_name="已发送")),
        client.FolderPlan(
            client.FolderInfo(name="Parent", display_name="Parent", selectable=False)
        ),
    ]
    results = client.sync_all(a_settings(tmp_path), plans, limit=10)

    assert len(fake_imap.instances) == 1, "a sweep must not log in once per folder"
    assert [result.folder.name for result in results] == ["INBOX", "Sent", "Parent"]
    assert results[0].snapshot is not None
    assert [message.uid for message in results[0].snapshot.fetched] == ["101", "102"]
    # A folder the server refuses to open is recorded and the sweep continues.
    assert results[2].snapshot is not None
    assert results[2].snapshot.fetched == []


def test_a_refused_folder_does_not_abandon_the_sweep(fake_imap, tmp_path: Path) -> None:
    del fake_imap.mailboxes["Sent"]
    plans = [
        client.FolderPlan(client.FolderInfo(name="INBOX", display_name="INBOX")),
        client.FolderPlan(client.FolderInfo(name="Sent", display_name="已发送")),
    ]
    results = client.sync_all(a_settings(tmp_path), plans, limit=10)

    assert results[0].snapshot is not None
    assert results[0].snapshot.fetched
    assert results[1].snapshot is None
    assert "imap select Sent failed" in results[1].error


def test_reading_one_message_returns_its_body(fake_imap, tmp_path: Path) -> None:
    message = client.fetch_body(a_settings(tmp_path), folder="INBOX", uid="102")
    assert message.uid == "102"
    assert "第二条消息的正文" in message.body


class FakePop3:
    instances: list["FakePop3"] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.messages = {1: b"uid-a", 2: b"uid-b"}
        self.top_calls: list[int] = []
        FakePop3.instances.append(self)

    def user(self, value: str) -> None:
        return None

    def pass_(self, value: str) -> None:
        return None

    def uidl(self, index=None):
        lines = [
            f"{number} {value.decode()}".encode()
            for number, value in sorted(self.messages.items())
        ]
        return b"+OK", lines, 0

    def top(self, index: int, lines: int):
        self.top_calls.append(index)
        header = HEADERS.encode()
        return b"+OK", header.split(b"\r\n")[:-1], len(header)

    def retr(self, index: int):
        raw = RAW
        return b"+OK", raw.split(b"\r\n"), len(raw)

    def stat(self):
        return len(self.messages), 0

    def quit(self) -> None:
        return None


def test_pop3_sync_fetches_headers_and_reports_removals(monkeypatch, tmp_path: Path) -> None:
    FakePop3.instances = []
    monkeypatch.setattr(client.poplib, "POP3_SSL", FakePop3)
    settings = a_settings(
        tmp_path,
        receive={
            "protocol": "pop3",
            "host": "pop.example.com",
            "port": 995,
            "security": "ssl",
        },
    )
    folders = client.list_folders(settings)
    assert [folder.name for folder in folders] == ["INBOX"]
    snapshot = client.sync_folder(
        settings, folders[0], known_uids={"uid-a", "uid-gone"}, limit=10
    )
    assert [message.uid for message in snapshot.fetched] == ["uid-b"]
    assert snapshot.removed == ["uid-gone"]
    # TOP (headers) is used, not RETR: a sync never downloads a body.
    assert FakePop3.instances[0].top_calls == [2]


def test_receiving_can_be_turned_off(tmp_path: Path) -> None:
    settings = a_settings(
        tmp_path,
        receive={"protocol": "none", "host": "imap.example.com", "port": 993},
    )
    with pytest.raises(client.MailNotConfigured):
        client.list_folders(settings)


# -- the cache -----------------------------------------------------------


@pytest_asyncio.fixture
async def cache(tmp_path: Path) -> MailCache:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'cache.db'}")
    await create_all(engine)
    yield MailCache(create_session_factory(engine))
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_snapshot_lands_in_the_cache(cache: MailCache) -> None:
    snapshot = client.FolderSnapshot(
        folder=client.FolderInfo(name="INBOX", display_name="INBOX"),
        uid_validity=42,
        total=2,
        fetched=[
            client.HeaderMessage(
                uid="1",
                subject="新设备登录提醒",
                sender="网易邮箱账号安全 <safe@service.example.com>",
                to="winnie@example.com",
                date="2026-11-02T22:00:08+08:00",
                message_id="<abc@example.com>",
                unread=False,
                size=100,
            )
        ],
        high_uid=1,
    )
    await cache.apply(OWNER, snapshot, protocol="imap")
    folders = await cache.folders(OWNER, "imap")
    assert folders[0]["uid_validity"] == 42
    assert folders[0]["last_uid"] == 1
    messages = await cache.list_messages(OWNER)
    assert messages[0]["subject"] == "新设备登录提醒"
    assert messages[0]["has_body"] is False
    # The header date is stored as naive UTC, the convention for every table.
    assert messages[0]["date"].startswith("2026-11-02T14:00:08")


@pytest.mark.asyncio
async def test_search_finds_subject_sender_and_body(cache: MailCache) -> None:
    snapshot = client.FolderSnapshot(
        folder=client.FolderInfo(name="INBOX", display_name="INBOX"),
        fetched=[
            client.HeaderMessage(
                uid="1",
                subject="Your invoice",
                sender="billing@example.com",
                to="winnie@example.com",
                date="2026-11-02T09:00:00+08:00",
                message_id="<x@example.com>",
                unread=True,
                size=10,
            )
        ],
        high_uid=1,
    )
    await cache.apply(OWNER, snapshot, protocol="imap")
    assert [row["uid"] for row in await cache.list_messages(OWNER, query="invoice")] == ["1"]
    assert [row["uid"] for row in await cache.list_messages(OWNER, query="billing")] == ["1"]
    assert await cache.list_messages(OWNER, query="nothing-matches") == []


@pytest.mark.asyncio
async def test_a_reset_drops_the_folder_before_recreating_it(cache: MailCache) -> None:
    folder = client.FolderInfo(name="INBOX", display_name="INBOX")
    await cache.apply(
        OWNER,
        client.FolderSnapshot(
            folder=folder,
            fetched=[
                client.HeaderMessage(
                    uid="1",
                    subject="old",
                    sender="a@example.com",
                    to="b@example.com",
                    date="",
                    message_id="",
                    unread=False,
                    size=0,
                )
            ],
            high_uid=1,
        ),
        protocol="imap",
    )
    await cache.apply(
        OWNER,
        client.FolderSnapshot(
            folder=folder,
            reset=True,
            fetched=[
                client.HeaderMessage(
                    uid="7",
                    subject="new",
                    sender="a@example.com",
                    to="b@example.com",
                    date="",
                    message_id="",
                    unread=False,
                    size=0,
                )
            ],
            high_uid=7,
        ),
        protocol="imap",
    )
    rows = await cache.list_messages(OWNER)
    assert [row["subject"] for row in rows] == ["new"]


@pytest.mark.asyncio
async def test_syncing_twice_keeps_the_cache(
    cache: MailCache, fake_imap, tmp_path: Path
) -> None:
    """The regression this guards: a stored *sequence number* looks like a uid.

    The second sync then sees no overlap, treats every cached message as deleted
    and deletes exactly the rows it just inserted. The first sync must answer
    with the server's uids, not with 1..N.
    """
    settings = a_settings(tmp_path)
    folder = client.FolderInfo(name="INBOX", display_name="INBOX")
    for _ in range(2):
        cached = await cache.folder(OWNER, "imap", "INBOX") or {}
        snapshot = client.sync_folder(
            settings,
            folder,
            known_uids=await cache.known_uids(OWNER, "imap", "INBOX"),
            last_uid=int(cached.get("last_uid") or 0),
            uid_validity=int(cached.get("uid_validity") or 0),
            limit=10,
        )
        assert snapshot.removed == []
        await cache.apply(OWNER, snapshot, protocol="imap")

    rows = await cache.list_messages(OWNER)
    assert [row["uid"] for row in rows] == ["102", "101"]
    assert (await cache.stats(OWNER))["messages"] == 2


# -- the HTTP surface ----------------------------------------------------


def app_for(tmp_path: Path):
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        auth=AuthConfig(enabled=False),
        # Meetings is enabled only because it is the cheapest capability that
        # makes the host build the application database the mail cache needs.
        meetings=MeetingsConfig(
            enabled=True,
            default_user_id="local",
            local_root=str(tmp_path / "meetings"),
            notes_root=str(tmp_path / "notes"),
            max_upload_mb=1,
        ),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'mail.db'}", auto_create_tables=True
        ),
        jobs=JobsConfig(backend="inline"),
        mail=MailConfig(enabled=True, root=str(tmp_path / "mail")),
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    return create_app(settings=settings, http=http), http


@pytest_asyncio.fixture
async def mail_client(tmp_path: Path):
    app, http = app_for(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as test_client:
            yield test_client
    await http.aclose()


@pytest.mark.asyncio
async def test_the_form_starts_empty(mail_client: httpx.AsyncClient) -> None:
    response = await mail_client.get("/api/v1/settings/mail")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["smtp"]["password_set"] is False
    assert "password" not in body["smtp"]


@pytest.mark.asyncio
async def test_saving_never_returns_the_password(mail_client: httpx.AsyncClient) -> None:
    saved = await mail_client.put("/api/v1/settings/mail", json=a_payload())
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["address"] == "winnie@example.com"
    assert body["smtp"]["password_set"] is True
    assert body["can_send"] is True
    assert "s3cret" not in saved.text

    reloaded = (await mail_client.get("/api/v1/settings/mail")).json()
    assert reloaded["smtp"]["password_set"] is True
    assert "s3cret" not in str(reloaded)


@pytest.mark.asyncio
async def test_a_test_reports_the_server_answer(
    mail_client: httpx.AsyncClient, monkeypatch
) -> None:
    await mail_client.put("/api/v1/settings/mail", json=a_payload())
    monkeypatch.setattr(client, "test_smtp", lambda settings: "smtp ok")
    response = await mail_client.post(
        "/api/v1/settings/mail/test", json={"target": "smtp"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "target": "smtp", "detail": "smtp ok"}


@pytest.mark.asyncio
async def test_a_rejected_login_is_a_400_not_a_logout(
    mail_client: httpx.AsyncClient, monkeypatch
) -> None:
    await mail_client.put("/api/v1/settings/mail", json=a_payload())

    def reject(settings):
        raise client.MailAuthError("imap rejected winnie@example.com")

    monkeypatch.setattr(client, "test_receive", reject)
    response = await mail_client.post(
        "/api/v1/settings/mail/test", json={"target": "receive"}
    )
    assert response.status_code == 400
    assert "imap rejected" in response.json()["detail"]


def a_snapshot(folder: str, uid: str, subject: str) -> client.FolderSnapshot:
    return client.FolderSnapshot(
        folder=client.FolderInfo(name=folder, display_name=folder),
        uid_validity=42,
        total=1,
        fetched=[
            client.HeaderMessage(
                uid=uid,
                subject=subject,
                sender="sender@example.com",
                to="winnie@example.com",
                date="2026-11-02T22:00:08+08:00",
                message_id=f"<{uid}@example.com>",
                unread=True,
                size=20,
            )
        ],
        high_uid=int(uid) if uid.isdigit() else 1,
    )


@pytest.mark.asyncio
async def test_sync_walks_every_folder_and_the_console_reads_it(
    mail_client: httpx.AsyncClient, monkeypatch
) -> None:
    await mail_client.put("/api/v1/settings/mail", json=a_payload())

    folders = [
        client.FolderInfo(name="INBOX", display_name="INBOX"),
        client.FolderInfo(name="Sent", display_name="已发送"),
        client.FolderInfo(name="Parent", display_name="Parent", selectable=False),
    ]
    monkeypatch.setattr(client, "list_folders", lambda settings: folders)

    def sync(settings, plans, *, limit):
        results = []
        for plan in plans:
            if plan.folder.name == "Sent":
                results.append(
                    client.FolderResult(plan.folder, a_snapshot("Sent", "9", "已发送的邮件"))
                )
            elif not plan.folder.selectable:
                results.append(
                    client.FolderResult(plan.folder, client.FolderSnapshot(folder=plan.folder))
                )
            else:
                results.append(
                    client.FolderResult(
                        plan.folder, a_snapshot("INBOX", "1", "新设备登录提醒")
                    )
                )
        return results

    monkeypatch.setattr(client, "sync_all", sync)

    synced = await mail_client.post("/api/v1/mail/sync")
    assert synced.status_code == 200, synced.text
    result = synced.json()
    assert result["folders"] == 3
    assert result["fetched"] == 2
    assert result["errors"] == []

    listing = await mail_client.get("/api/v1/mail/folders")
    assert listing.status_code == 200
    names = [folder["name"] for folder in listing.json()["folders"]]
    assert names == ["INBOX", "Parent", "Sent"]
    assert listing.json()["stats"]["messages"] == 2

    everything = await mail_client.get("/api/v1/mail/messages")
    assert len(everything.json()) == 2
    sent = await mail_client.get("/api/v1/mail/messages?folder=Sent")
    assert [row["subject"] for row in sent.json()] == ["已发送的邮件"]
    found = await mail_client.get("/api/v1/mail/messages?q=登录")
    assert [row["uid"] for row in found.json()] == ["1"]


@pytest.mark.asyncio
async def test_opening_a_message_downloads_its_body_once(
    mail_client: httpx.AsyncClient, monkeypatch
) -> None:
    await mail_client.put("/api/v1/settings/mail", json=a_payload())
    monkeypatch.setattr(
        client,
        "list_folders",
        lambda settings: [client.FolderInfo(name="INBOX", display_name="INBOX")],
    )
    monkeypatch.setattr(
        client,
        "sync_all",
        lambda settings, plans, *, limit: [
            client.FolderResult(plan.folder, a_snapshot("INBOX", "1", "新设备登录提醒"))
            for plan in plans
        ],
    )
    await mail_client.post("/api/v1/mail/sync")
    cached = (await mail_client.get("/api/v1/mail/messages")).json()
    assert cached[0]["has_body"] is False

    calls: list[str] = []

    def body(settings, *, folder, uid):
        calls.append(uid)
        return client.MailMessage(
            uid=uid,
            subject="新设备登录提醒",
            sender="sender@example.com",
            to="winnie@example.com",
            date="2026-11-02T22:00:08+08:00",
            snippet="body snippet",
            body="the whole body",
            unread=True,
            message_id="<1@example.com>",
        )

    monkeypatch.setattr(client, "fetch_body", body)
    first = await mail_client.get(f"/api/v1/mail/messages/{cached[0]['id']}")
    assert first.status_code == 200, first.text
    assert first.json()["body"] == "the whole body"
    second = await mail_client.get(f"/api/v1/mail/messages/{cached[0]['id']}")
    assert second.json()["body"] == "the whole body"
    assert calls == ["1"], "the second open must read the cache, not the server"


@pytest.mark.asyncio
async def test_an_unknown_message_is_a_404(mail_client: httpx.AsyncClient) -> None:
    response = await mail_client.get(
        "/api/v1/mail/messages/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_an_unconfigured_mailbox_is_a_400(mail_client: httpx.AsyncClient) -> None:
    response = await mail_client.post("/api/v1/mail/sync")
    assert response.status_code == 400
    assert "receiving is not enabled" in response.json()["detail"]


@pytest.mark.asyncio
async def test_clearing_the_account_also_clears_the_cache(
    mail_client: httpx.AsyncClient, monkeypatch
) -> None:
    await mail_client.put("/api/v1/settings/mail", json=a_payload())
    monkeypatch.setattr(
        client,
        "list_folders",
        lambda settings: [client.FolderInfo(name="INBOX", display_name="INBOX")],
    )
    monkeypatch.setattr(
        client,
        "sync_all",
        lambda settings, plans, *, limit: [
            client.FolderResult(plan.folder, a_snapshot("INBOX", "1", "hello"))
            for plan in plans
        ],
    )
    await mail_client.post("/api/v1/mail/sync")
    assert len((await mail_client.get("/api/v1/mail/messages")).json()) == 1
    cleared = await mail_client.delete("/api/v1/settings/mail")
    assert cleared.json() == {"cleared": True}
    assert (await mail_client.get("/api/v1/mail/messages")).json() == []
