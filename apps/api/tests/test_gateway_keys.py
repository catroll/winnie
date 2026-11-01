"""Gateway keys: the file store, per-account ownership and the console API."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from winnie_api.main import create_app
from winnie_api.settings import DatabaseConfig, RouteConfig, Settings
from winnie_core.config import AuthConfig, JobsConfig
from winnie_gateway.keystore import (
    GatewayKeyError,
    GatewayKeyStore,
    UnknownGatewayKey,
)

CHAT = {"model": "fake-model", "messages": [{"role": "user", "content": "ping"}]}
PASSWORD = "correct horse battery"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: httpx.AsyncClient, username: str) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def test_a_new_key_is_usable_and_only_its_digest_is_stored(tmp_path: Path) -> None:
    path = tmp_path / "gateway-keys.yaml"
    store = GatewayKeyStore(path)

    key, secret = store.create(label="laptop")

    assert secret.startswith("wsk_")
    assert store.verify(secret) == key
    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    assert hashlib.sha256(secret.encode()).hexdigest() in raw
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_missing_file_is_an_empty_store(tmp_path: Path) -> None:
    store = GatewayKeyStore(tmp_path / "absent.yaml")
    assert store.list() == []
    assert store.verify("wsk_something") is None


def test_revoking_makes_the_secret_stop_working(tmp_path: Path) -> None:
    store = GatewayKeyStore(tmp_path / "keys.yaml")
    key, secret = store.create(label="laptop")

    revoked = store.revoke(key.id)

    assert revoked.revoked_at
    assert store.verify(secret) is None
    assert store.revoke(key.id).revoked_at == revoked.revoked_at  # idempotent
    assert [item.id for item in store.list()] == [key.id]


def test_an_allowlist_limits_routes(tmp_path: Path) -> None:
    store = GatewayKeyStore(tmp_path / "keys.yaml")
    key, secret = store.create(label="phone", routes=["deepseek-openai", "deepseek-openai"])

    assert key.routes == ["deepseek-openai"]
    assert key.allows("deepseek-openai")
    assert not key.allows("openai")
    assert store.verify(secret) is not None


def test_an_unknown_id_is_reported(tmp_path: Path) -> None:
    store = GatewayKeyStore(tmp_path / "keys.yaml")
    with pytest.raises(UnknownGatewayKey):
        store.revoke("nope")


def test_an_empty_label_is_rejected(tmp_path: Path) -> None:
    store = GatewayKeyStore(tmp_path / "keys.yaml")
    with pytest.raises(GatewayKeyError):
        store.create(label="   ")


def test_a_second_store_sees_a_new_key_without_a_restart(tmp_path: Path) -> None:
    first = GatewayKeyStore(tmp_path / "keys.yaml")
    second = GatewayKeyStore(tmp_path / "keys.yaml")
    key, secret = first.create(label="laptop")

    assert second.find(key.id).label == "laptop"
    assert second.verify(secret) is not None


def test_a_broken_file_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "keys.yaml"
    path.write_text("version: 1\nkeys:\n- id: x\n", encoding="utf-8")
    with pytest.raises(GatewayKeyError):
        GatewayKeyStore(path)


async def test_the_console_creates_lists_and_revokes_a_key(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post("/api/v1/gateway/keys", json={"label": "console"})
    assert created.status_code == 201, created.text
    body = created.json()
    secret = body["secret"]
    key_id = body["key"]["id"]
    assert secret.startswith("wsk_")
    assert body["key"]["routes"] == []
    # An auth-disabled deployment acts as the local operator.
    assert body["key"]["owner_id"] == "local"
    assert body["key"]["owner_name"] == "local"

    listed = await client.get("/api/v1/gateway/keys")
    assert listed.status_code == 200
    # The console builds its allowlist checkboxes from these names.
    assert listed.json()["routes"] == ["fake-anthropic", "fake-openai"]
    rows = listed.json()["keys"]
    row = next(item for item in rows if item["id"] == key_id)
    assert row["label"] == "console"
    assert "digest" not in row
    assert "secret" not in row
    assert all("secret" not in item for item in rows)

    # The key really opens the data plane.
    used = await client.post(
        "/fake-openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {secret}"},
        json=CHAT,
    )
    assert used.status_code == 200

    revoked = await client.delete(f"/api/v1/gateway/keys/{key_id}")
    assert revoked.status_code == 200
    assert revoked.json()["key"]["revoked_at"]

    denied = await client.post(
        "/fake-openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {secret}"},
        json=CHAT,
    )
    assert denied.status_code == 401


async def test_an_allowlist_must_name_configured_routes(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/gateway/keys",
        json={"label": "typo", "routes": ["deepseek-openai"]},
    )
    assert response.status_code == 400
    assert "unknown route" in response.json()["detail"]

    good = await client.post(
        "/api/v1/gateway/keys",
        json={"label": "scoped", "routes": ["fake-openai"]},
    )
    assert good.status_code == 201
    assert good.json()["key"]["routes"] == ["fake-openai"]


async def test_revoking_an_unknown_key_is_404(client: httpx.AsyncClient) -> None:
    response = await client.delete("/api/v1/gateway/keys/does-not-exist")
    assert response.status_code == 404


def test_the_owner_survives_a_reload(tmp_path: Path) -> None:
    path = tmp_path / "keys.yaml"
    key, secret = GatewayKeyStore(path).create(
        label="laptop", owner_id="u-1", owner_name="alice"
    )

    reloaded = GatewayKeyStore(path).find(key.id)

    assert reloaded.owner_id == "u-1"
    assert reloaded.owner_name == "alice"
    assert reloaded.view()["owner_name"] == "alice"
    assert GatewayKeyStore(path).verify(secret) is not None


def test_a_file_without_owners_still_loads(tmp_path: Path) -> None:
    """Keys written before ownership existed keep loading, owned by nobody."""
    path = tmp_path / "keys.yaml"
    path.write_text(
        "version: 1\n"
        "keys:\n"
        "- id: a1b2c3d4\n"
        "  label: legacy\n"
        f"  digest: {hashlib.sha256(b'wsk_legacy').hexdigest()}\n"
        "  created_at: '2026-11-01T00:00:00+00:00'\n",
        encoding="utf-8",
    )

    key = GatewayKeyStore(path).list()[0]

    assert key.owner_id == ""
    assert key.owner_name == ""


@pytest_asyncio.fixture
async def accounts_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "fake-vendor-key")
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        gateway_keys_path=str(tmp_path / "gateway-keys.yaml"),
        routes={
            "fake-openai": RouteConfig(
                protocol="openai", base_url="https://fake.openai", vendor="fake"
            )
        },
        auth=AuthConfig(enabled=True, allow_registration=True),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'keys.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True})),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        yield app
    await http.aclose()


@pytest_asyncio.fixture
async def accounts_client(accounts_app) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=accounts_app), base_url="http://test"
    ) as test_client:
        yield test_client


async def test_each_account_manages_only_its_own_keys(
    accounts_client: httpx.AsyncClient,
) -> None:
    alice = await _register(accounts_client, "alice")  # first account: admin
    bob = await _register(accounts_client, "bob")  # later account: user

    alice_key = await accounts_client.post(
        "/api/v1/gateway/keys", headers=_bearer(alice), json={"label": "alice-laptop"}
    )
    bob_key = await accounts_client.post(
        "/api/v1/gateway/keys", headers=_bearer(bob), json={"label": "bob-phone"}
    )
    assert alice_key.status_code == 201, alice_key.text
    assert bob_key.status_code == 201, bob_key.text
    alice_id = alice_key.json()["key"]["id"]
    bob_id = bob_key.json()["key"]["id"]
    assert alice_key.json()["key"]["owner_name"] == "alice"
    assert bob_key.json()["key"]["owner_name"] == "bob"

    bob_list = await accounts_client.get(
        "/api/v1/gateway/keys", headers=_bearer(bob)
    )
    assert [key["id"] for key in bob_list.json()["keys"]] == [bob_id]

    forbidden = await accounts_client.get(
        "/api/v1/gateway/keys?scope=all", headers=_bearer(bob)
    )
    assert forbidden.status_code == 403

    other = await accounts_client.delete(
        f"/api/v1/gateway/keys/{alice_id}", headers=_bearer(bob)
    )
    assert other.status_code == 404

    alice_list = await accounts_client.get(
        "/api/v1/gateway/keys?scope=all", headers=_bearer(alice)
    )
    names = {key["id"]: key["owner_name"] for key in alice_list.json()["keys"]}
    assert names == {alice_id: "alice", bob_id: "bob"}

    revoked = await accounts_client.delete(
        f"/api/v1/gateway/keys/{bob_id}", headers=_bearer(alice)
    )
    assert revoked.status_code == 200
    assert revoked.json()["key"]["revoked_at"]

    bob_after = await accounts_client.get(
        "/api/v1/gateway/keys", headers=_bearer(bob)
    )
    assert bob_after.json()["keys"][0]["revoked_at"]


async def test_an_ownerless_key_is_administrator_only(
    accounts_client: httpx.AsyncClient,
    accounts_app,
) -> None:
    legacy, _ = accounts_app.state.gateway_keys.create(label="legacy")
    alice = await _register(accounts_client, "alice")
    bob = await _register(accounts_client, "bob")

    bob_list = await accounts_client.get("/api/v1/gateway/keys", headers=_bearer(bob))
    assert bob_list.json()["keys"] == []

    alice_list = await accounts_client.get("/api/v1/gateway/keys", headers=_bearer(alice))
    assert [key["id"] for key in alice_list.json()["keys"]] == [legacy.id]

    denied = await accounts_client.delete(
        f"/api/v1/gateway/keys/{legacy.id}", headers=_bearer(bob)
    )
    assert denied.status_code == 404

    allowed = await accounts_client.delete(
        f"/api/v1/gateway/keys/{legacy.id}", headers=_bearer(alice)
    )
    assert allowed.status_code == 200


async def test_listing_and_creating_need_a_session(
    accounts_client: httpx.AsyncClient,
) -> None:
    listed = await accounts_client.get("/api/v1/gateway/keys")
    assert listed.status_code == 401
    created = await accounts_client.post(
        "/api/v1/gateway/keys", json={"label": "anonymous"}
    )
    assert created.status_code == 401
