from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from winnie_core.auth.errors import UserNotFound
from winnie_core.auth.service import AuthService
from winnie_api.main import create_app
from winnie_api.runtime import build_runtime
from winnie_api.settings import Settings

PASSWORD = "correct horse battery"


def _database_path(settings: Settings) -> Path:
    url = settings.resolve_database_url()
    _, _, raw_path = url.partition("sqlite+aiosqlite:///")
    return Path(raw_path)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(
    client: httpx.AsyncClient,
    username: str,
    password: str = PASSWORD,
) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_register_login_logout(auth_client: httpx.AsyncClient) -> None:
    registered = await _register(auth_client, "Alice")
    token = registered["access_token"]
    assert registered["token_type"] == "bearer"
    assert registered["user"]["username"] == "alice"
    assert registered["user"]["id"]

    second = await auth_client.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": PASSWORD},
    )
    assert second.status_code == 403

    me = await auth_client.get("/api/v1/auth/me", headers=_bearer(token))
    assert me.status_code == 200
    assert me.json()["username"] == "alice"

    wrong = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wrong password"},
    )
    assert wrong.status_code == 401

    login = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": PASSWORD},
    )
    assert login.status_code == 200
    login_token = login.json()["access_token"]

    logout = await auth_client.post(
        "/api/v1/auth/logout", headers=_bearer(login_token)
    )
    assert logout.status_code == 204
    after = await auth_client.get(
        "/api/v1/auth/me", headers=_bearer(login_token)
    )
    assert after.status_code == 401


async def test_change_password_invalidates_sessions(
    account_client: httpx.AsyncClient,
) -> None:
    registered = await _register(account_client, "alice")
    token = registered["access_token"]

    wrong = await account_client.post(
        "/api/v1/auth/password",
        headers=_bearer(token),
        json={"current_password": "wrong password", "new_password": "new password"},
    )
    assert wrong.status_code == 401
    still_valid = await account_client.get(
        "/api/v1/auth/me", headers=_bearer(token)
    )
    assert still_valid.status_code == 200

    changed = await account_client.post(
        "/api/v1/auth/password",
        headers=_bearer(token),
        json={"current_password": PASSWORD, "new_password": "new password"},
    )
    assert changed.status_code == 204

    old_session = await account_client.get(
        "/api/v1/auth/me", headers=_bearer(token)
    )
    assert old_session.status_code == 401

    old_password = await account_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": PASSWORD},
    )
    assert old_password.status_code == 401

    new_password = await account_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "new password"},
    )
    assert new_password.status_code == 200


async def test_duplicate_username(account_client: httpx.AsyncClient) -> None:
    await _register(account_client, "alice")
    duplicate = await account_client.post(
        "/api/v1/auth/register",
        json={"username": "ALICE", "password": PASSWORD},
    )
    assert duplicate.status_code == 409


async def test_invalid_registration_payload(auth_client: httpx.AsyncClient) -> None:
    short_password = await auth_client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "short"},
    )
    assert short_password.status_code == 422

    bad_username = await auth_client.post(
        "/api/v1/auth/register",
        json={"username": "bad name", "password": PASSWORD},
    )
    assert bad_username.status_code == 422


async def test_meetings_are_isolated_by_account(
    account_client: httpx.AsyncClient,
) -> None:
    alice = await _register(account_client, "alice")
    bob = await _register(account_client, "bob")
    alice_headers = _bearer(alice["access_token"])
    bob_headers = _bearer(bob["access_token"])

    unauthenticated = await account_client.get("/api/v1/meetings")
    assert unauthenticated.status_code == 401

    created = await account_client.post(
        "/api/v1/meetings",
        json={"title": "Alice meeting"},
        headers=alice_headers,
    )
    assert created.status_code == 201
    meeting_id = created.json()["id"]

    alice_list = await account_client.get(
        "/api/v1/meetings", headers=alice_headers
    )
    assert alice_list.json()["total"] == 1

    bob_list = await account_client.get(
        "/api/v1/meetings", headers=bob_headers
    )
    assert bob_list.json()["total"] == 0

    hidden = await account_client.get(
        f"/api/v1/meetings/{meeting_id}", headers=bob_headers
    )
    assert hidden.status_code == 404

    delete = await account_client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=bob_headers
    )
    assert delete.status_code == 404

    visible = await account_client.get(
        f"/api/v1/meetings/{meeting_id}", headers=alice_headers
    )
    assert visible.status_code == 200


async def test_secrets_are_hashed(
    auth_client: httpx.AsyncClient,
    auth_settings: Settings,
) -> None:
    registered = await _register(auth_client, "alice")
    token = registered["access_token"]

    with sqlite3.connect(_database_path(auth_settings)) as connection:
        password_hash = connection.execute(
            "select password_hash from users where username = 'alice'"
        ).fetchone()[0]
        token_hash = connection.execute(
            "select token_hash from auth_sessions"
        ).fetchone()[0]

    assert password_hash.startswith("$argon2id$")
    assert PASSWORD not in password_hash
    assert token_hash != token
    assert len(token_hash) == 64


async def test_expired_session_is_rejected(
    auth_client: httpx.AsyncClient,
    auth_settings: Settings,
) -> None:
    registered = await _register(auth_client, "alice")
    token = registered["access_token"]

    with sqlite3.connect(_database_path(auth_settings)) as connection:
        connection.execute(
            "update auth_sessions set expires_at = '2000-01-01 00:00:00'"
        )
        connection.commit()

    response = await auth_client.get(
        "/api/v1/auth/me", headers=_bearer(token)
    )
    assert response.status_code == 401


async def test_auth_routes_are_absent_when_disabled(
    meeting_client: httpx.AsyncClient,
) -> None:
    response = await meeting_client.get("/api/v1/auth/me")
    assert response.status_code == 404


async def test_cleanup_deletes_only_expired_sessions(
    auth_client: httpx.AsyncClient,
    auth_settings: Settings,
) -> None:
    await _register(auth_client, "alice")
    login = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": PASSWORD},
    )
    assert login.status_code == 200

    with sqlite3.connect(_database_path(auth_settings)) as connection:
        connection.execute(
            "update auth_sessions set expires_at = '2000-01-01 00:00:00' "
            "where id = (select min(id) from auth_sessions)"
        )
        connection.commit()

    engine = create_async_engine(auth_settings.resolve_database_url())
    service = AuthService(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        config=auth_settings.auth,
    )
    try:
        deleted = await service.cleanup_expired_sessions()
    finally:
        await engine.dispose()

    assert deleted == 1
    with sqlite3.connect(_database_path(auth_settings)) as connection:
        remaining = connection.execute(
            "select count(*) from auth_sessions"
        ).fetchone()[0]
    assert remaining == 1


async def test_login_lockout_expires_and_resets(
    auth_settings: Settings,
) -> None:
    settings = auth_settings.model_copy(
        update={
            "auth": auth_settings.auth.model_copy(
                update={
                    "login_max_attempts": 2,
                    "login_window_seconds": 60,
                    "login_lockout_seconds": 120,
                }
            )
        }
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await _register(client, "dave")
                for _ in range(2):
                    failed = await client.post(
                        "/api/v1/auth/login",
                        json={"username": "dave", "password": "wrong password"},
                    )
                    assert failed.status_code == 401

                locked = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "dave", "password": PASSWORD},
                )
                assert locked.status_code == 429
                assert int(locked.headers["Retry-After"]) >= 1

                database = _database_path(settings)
                with sqlite3.connect(database) as connection:
                    connection.execute(
                        "update login_throttles "
                        "set locked_until = '2000-01-01 00:00:00'"
                    )

                recovered = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "dave", "password": PASSWORD},
                )
                assert recovered.status_code == 200

                reset = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "dave", "password": "wrong password"},
                )
                assert reset.status_code == 401
    finally:
        await http.aclose()


async def test_reset_password_revokes_existing_sessions(
    auth_client: httpx.AsyncClient,
    auth_settings: Settings,
) -> None:
    registered = await _register(auth_client, "alice")
    token = registered["access_token"]

    runtime = build_runtime(auth_settings)
    assert runtime is not None and runtime.auth_service is not None
    await runtime.startup()
    try:
        await runtime.auth_service.reset_password(
            username="alice",
            new_password="new password",
        )
        with pytest.raises(UserNotFound):
            await runtime.auth_service.reset_password(
                username="missing",
                new_password="new password",
            )
    finally:
        await runtime.shutdown()

    old_session = await auth_client.get(
        "/api/v1/auth/me", headers=_bearer(token)
    )
    assert old_session.status_code == 401

    old_password = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": PASSWORD},
    )
    assert old_password.status_code == 401

    new_password = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "new password"},
    )
    assert new_password.status_code == 200
