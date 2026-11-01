"""Gateway keys: the file store and the console API that manages it."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from winnie_gateway.keystore import (
    GatewayKeyError,
    GatewayKeyStore,
    UnknownGatewayKey,
)

CHAT = {"model": "fake-model", "messages": [{"role": "user", "content": "ping"}]}


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

    listed = await client.get("/api/v1/gateway/keys")
    assert listed.status_code == 200
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
