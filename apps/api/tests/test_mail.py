"""The mailbox: per-user storage, the three protocols, and the settings API.

The protocol clients are exercised through fakes of ``smtplib``/``imaplib``/
``poplib`` rather than a live server: what matters here is the shape of what we
send and what we store, and the two provider quirks that once looked like a bad
password. A live-account check is a manual step, documented in docs/mail.md.
"""

from __future__ import annotations

import stat
from pathlib import Path

import httpx
import pytest

from winnie_api.main import create_app
from winnie_api.settings import DatabaseConfig, Settings
from winnie_core.config import AuthConfig, JobsConfig
from winnie_mail import client
from winnie_mail.config import MailConfig
from winnie_mail.schemas import MailUpdate
from winnie_mail.service import MailService
from winnie_mail.settings import MailSettings, MailSettingsStore

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

RAW = (
    "From: =?utf-8?b?572R5piT6YKu566x6LSm5Y+35a6J5YWo?= <safe@service.example.com>\r\n"
    "To: winnie@example.com\r\n"
    "Subject: =?utf-8?b?5paw6K6+5aSH55m75b2V5o+Q6YaS?=\r\n"
    "Date: Mon, 2 Nov 2026 22:00:08 +0800\r\n"
    "Message-ID: <abc@example.com>\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "Content-Transfer-Encoding: 8bit\r\n"
    "\r\n"
    "您的邮箱刚刚在新设备上登录，如非本人请修改密码。\r\n"
).encode("utf-8")


class FakeImap:
    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.capabilities = ("IMAP4rev1", "ID")
        self.sent_identity = False

    def login(self, user: str, password: str) -> None:
        assert (user, password) == ("winnie@example.com", "s3cret")

    def _simple_command(self, name, *args):
        assert name == "ID"
        self.sent_identity = True
        return "OK", [b"ID completed"]

    def select(self, folder, readonly=False):
        assert folder == "INBOX"
        assert readonly is True
        assert self.sent_identity, "126 refuses SELECT until the ID command is sent"
        return "OK", [b"2"]

    def search(self, charset, criteria):
        return "OK", [b"1 2"]

    def fetch(self, identifier, spec):
        flags = b"\\Seen" if identifier == b"2" else b""
        return "OK", [(b"1 (FLAGS (" + flags + b") BODY[] {123}", RAW), b")"]

    def logout(self) -> None:
        return None


def test_imap_sends_the_id_command_and_decodes_the_message(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(client.imaplib, "IMAP4_SSL", FakeImap)
    messages = client.fetch_recent(a_settings(tmp_path), limit=2)

    assert len(messages) == 2
    newest = messages[0]
    assert newest.subject == "新设备登录提醒"
    # The decoded name must not come back as another encoded-word.
    assert newest.sender == "网易邮箱账号安全 <safe@service.example.com>"
    assert newest.unread is False
    assert "新设备上登录" in newest.snippet
    assert newest.message_id == "<abc@example.com>"


def test_pages_are_read_instead_of_marked_read(monkeypatch, tmp_path: Path) -> None:
    seen: list[tuple] = []

    class ReadOnly(FakeImap):
        def select(self, folder, readonly=False):
            seen.append((folder, readonly))
            return super().select(folder, readonly)

    monkeypatch.setattr(client.imaplib, "IMAP4_SSL", ReadOnly)
    client.fetch_recent(a_settings(tmp_path), limit=1)
    assert seen == [("INBOX", True)]


class FakePop3:
    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port

    def user(self, value: str) -> None:
        return None

    def pass_(self, value: str) -> None:
        return None

    def stat(self):
        return 1, len(RAW)

    def retr(self, index: int):
        return b"+OK", RAW.split(b"\r\n"), len(RAW)

    def uidl(self, index: int) -> bytes:
        return b"1 uid-42"

    def quit(self) -> None:
        return None


def test_pop3_reads_the_newest_message(monkeypatch, tmp_path: Path) -> None:
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
    messages = client.fetch_recent(settings, limit=5)
    assert [message.uid for message in messages] == ["uid-42"]
    assert messages[0].subject == "新设备登录提醒"


def test_receiving_can_be_turned_off(tmp_path: Path) -> None:
    settings = a_settings(
        tmp_path,
        receive={"protocol": "none", "host": "imap.example.com", "port": 993},
    )
    with pytest.raises(client.MailNotConfigured):
        client.fetch_recent(settings, limit=1)


# -- the HTTP surface ----------------------------------------------------


def app_for(tmp_path: Path):
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        auth=AuthConfig(enabled=False),
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


@pytest.fixture
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


@pytest.mark.asyncio
async def test_the_message_list_is_masked_to_a_view(
    mail_client: httpx.AsyncClient, monkeypatch
) -> None:
    await mail_client.put("/api/v1/settings/mail", json=a_payload())
    monkeypatch.setattr(
        client,
        "fetch_recent",
        lambda settings, limit=10: [
            client.MailMessage(
                uid="7",
                subject="Hi",
                sender="a@example.com",
                to="winnie@example.com",
                date="2026-11-02T22:00:08+08:00",
                snippet="hello",
                body="hello there",
                unread=True,
                message_id="<x@example.com>",
            )
        ][:limit],
    )
    response = await mail_client.get("/api/v1/settings/mail/messages?limit=5")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["protocol"] == "imap"
    assert body["messages"][0]["uid"] == "7"
    assert body["messages"][0]["unread"] is True


@pytest.mark.asyncio
async def test_an_unconfigured_mailbox_is_a_400(mail_client: httpx.AsyncClient) -> None:
    response = await mail_client.get("/api/v1/settings/mail/messages")
    assert response.status_code == 400
    assert "receive.host is required" in response.json()["detail"]
