"""Role model and operator-only user management over the real ASGI app."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio

from winnie_api.main import create_app
from winnie_api.settings import AIConfig, DatabaseConfig, IndexConfig, Settings
from winnie_core.config import AuthConfig, JobsConfig
from winnie_core.usage.config import UsageConfig
from winnie_gateway.config import RouteConfig
from winnie_news.config import NewsConfig

PASSWORD = "correct horse battery"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(
    client: httpx.AsyncClient,
    username: str,
    password: str = PASSWORD,
    display_name: str | None = None,
) -> dict:
    payload: dict[str, object] = {"username": username, "password": password}
    if display_name is not None:
        payload["display_name"] = display_name
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _login(
    client: httpx.AsyncClient,
    username: str,
    password: str = PASSWORD,
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )


@pytest_asyncio.fixture
async def operator_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """Accounts plus every operator feature enabled, with fake upstreams."""

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/admin/"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500)

    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={
            "fake-openai": RouteConfig(
                protocol="openai", base_url="https://fake.openai"
            )
        },
        ai=AIConfig(enabled=True, path=str(tmp_path / "ai.yaml")),
        news=NewsConfig(enabled=True, root=str(tmp_path / "news")),
        index=IndexConfig(enabled=True, base_url="http://index.local:8787"),
        usage=UsageConfig(enabled=True, root=str(tmp_path / "usage")),
        auth=AuthConfig(enabled=True, allow_registration=True),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'operator.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    await http.aclose()


async def test_first_registration_is_admin_and_later_registrations_are_users(
    account_client: httpx.AsyncClient,
) -> None:
    alice = await _register(account_client, "alice")
    assert alice["user"]["role"] == "admin"

    bob = await _register(account_client, "bob")
    assert bob["user"]["role"] == "user"

    # The role is part of the ordinary account surface, not only the admin API.
    me = await account_client.get(
        "/api/v1/auth/me", headers=_bearer(alice["access_token"])
    )
    assert me.status_code == 200
    assert me.json()["role"] == "admin"

    alice_login = await _login(account_client, "alice")
    assert alice_login.status_code == 200
    assert alice_login.json()["user"]["role"] == "admin"

    bob_login = await _login(account_client, "bob")
    assert bob_login.status_code == 200
    assert bob_login.json()["user"]["role"] == "user"


async def test_bootstrap_admin_when_registration_is_closed(
    auth_client: httpx.AsyncClient,
) -> None:
    # ``auth_client`` has allow_registration=False. The first account must still
    # be allowed and must own the operator surface; the second is refused.
    alice = await _register(auth_client, "alice")
    assert alice["user"]["role"] == "admin"

    blocked = await auth_client.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": PASSWORD},
    )
    assert blocked.status_code == 403


async def test_operator_routes_reject_a_normal_user(
    operator_client: httpx.AsyncClient,
) -> None:
    alice = await _register(operator_client, "alice")
    bob = await _register(operator_client, "bob")
    bob_headers = _bearer(bob["access_token"])
    alice_headers = _bearer(alice["access_token"])
    bob_id = bob["user"]["id"]

    # Every operator router rejects the normal user before it can answer
    # "feature disabled" or forward a request; the gate is a router-level
    # dependency, not a check inside one endpoint.
    reads = [
        "/api/v1/admin/users",
        "/api/v1/ai/config",
        "/api/v1/index/status",
        "/api/v1/usage/overview",
        "/api/v1/news/status",
        "/routes",
    ]
    for path in reads:
        forbidden = await operator_client.get(path, headers=bob_headers)
        assert forbidden.status_code == 403, (path, forbidden.text)
        assert "administrator" in forbidden.json()["detail"]
        allowed = await operator_client.get(path, headers=alice_headers)
        assert allowed.status_code == 200, (path, allowed.text)

    forbidden_writes = [
        ("POST", "/api/v1/admin/users", {"username": "mallory"}),
        ("PATCH", f"/api/v1/admin/users/{bob_id}", {"display_name": "Bob"}),
        ("POST", f"/api/v1/admin/users/{bob_id}/reset-password", None),
        ("POST", f"/api/v1/admin/users/{bob_id}/activate", None),
        ("POST", f"/api/v1/admin/users/{bob_id}/deactivate", None),
    ]
    for method, path, body in forbidden_writes:
        response = await operator_client.request(
            method, path, headers=bob_headers, json=body
        )
        assert response.status_code == 403, (method, path, response.text)

    listing = await operator_client.get(
        "/api/v1/admin/users", headers=alice_headers
    )
    assert listing.json()["total"] == 2


async def test_create_user_returns_a_temporary_password_once(
    account_client: httpx.AsyncClient,
) -> None:
    alice = await _register(account_client, "alice")
    headers = _bearer(alice["access_token"])

    created = await account_client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": "Charlie",
            "display_name": "Charlie Brown",
            "role": "user",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["user"]["username"] == "charlie"
    assert body["user"]["display_name"] == "Charlie Brown"
    assert body["user"]["role"] == "user"
    assert body["user"]["is_active"] is True
    password = body["temporary_password"]
    assert len(password) >= 8
    assert "password_hash" not in body["user"]
    assert "temporary_password" not in body["user"]

    login = await _login(account_client, "charlie", password)
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "user"

    duplicate = await account_client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={"username": "CHARLIE"},
    )
    assert duplicate.status_code == 409

    invalid_role = await account_client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={"username": "dave", "role": "owner"},
    )
    assert invalid_role.status_code == 422

    listing = await account_client.get(
        "/api/v1/admin/users", headers=headers
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 2
    items = {item["username"]: item for item in listing.json()["items"]}
    # Registration and login both create sessions, so the summary is live.
    assert items["alice"]["active_sessions"] >= 1
    assert items["alice"]["last_session_at"] is not None
    assert items["charlie"]["active_sessions"] >= 1
    assert items["charlie"]["last_session_at"] is not None
    serialized = listing.text
    assert "password_hash" not in serialized
    assert "temporary_password" not in serialized


async def test_list_search_and_pagination(
    account_client: httpx.AsyncClient,
) -> None:
    alice = await _register(account_client, "alice")
    headers = _bearer(alice["access_token"])
    for index, name in enumerate(["bob", "carol", "dave", "erin"]):
        created = await account_client.post(
            "/api/v1/admin/users",
            headers=headers,
            json={"username": name, "display_name": f"User {name.upper()}"},
        )
        assert created.status_code == 201, created.text
        if index == 3:
            assert created.json()["user"]["role"] == "user"

    first_page = await account_client.get(
        "/api/v1/admin/users?limit=2&offset=0", headers=headers
    )
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 5
    assert first_page.json()["limit"] == 2
    assert len(first_page.json()["items"]) == 2

    second_page = await account_client.get(
        "/api/v1/admin/users?limit=2&offset=2", headers=headers
    )
    third_page = await account_client.get(
        "/api/v1/admin/users?limit=2&offset=4", headers=headers
    )
    assert len(second_page.json()["items"]) == 2
    assert len(third_page.json()["items"]) == 1
    ids = [
        item["id"]
        for page in (first_page, second_page, third_page)
        for item in page.json()["items"]
    ]
    assert len(ids) == len(set(ids)) == 5

    by_username = await account_client.get(
        "/api/v1/admin/users?q=ali", headers=headers
    )
    assert by_username.json()["total"] == 1
    assert by_username.json()["items"][0]["username"] == "alice"

    by_display_name = await account_client.get(
        "/api/v1/admin/users?q=user%20dave", headers=headers
    )
    assert by_display_name.json()["total"] == 1
    assert by_display_name.json()["items"][0]["username"] == "dave"

    admins = await account_client.get(
        "/api/v1/admin/users?role=admin", headers=headers
    )
    assert admins.json()["total"] == 1
    assert admins.json()["items"][0]["username"] == "alice"

    inactive = await account_client.get(
        "/api/v1/admin/users?is_active=false", headers=headers
    )
    assert inactive.json()["total"] == 0


async def test_last_admin_and_self_deactivation_guards(
    account_client: httpx.AsyncClient,
) -> None:
    alice = await _register(account_client, "alice")
    alice_id = alice["user"]["id"]
    headers = _bearer(alice["access_token"])

    # The only active administrator cannot demote themselves out of the role.
    demote = await account_client.patch(
        f"/api/v1/admin/users/{alice_id}",
        headers=headers,
        json={"role": "user"},
    )
    assert demote.status_code == 409

    # Deactivating the caller is refused even when another administrator exists.
    self_deactivate = await account_client.patch(
        f"/api/v1/admin/users/{alice_id}",
        headers=headers,
        json={"is_active": False},
    )
    assert self_deactivate.status_code == 400

    created = await account_client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={"username": "carol", "role": "admin"},
    )
    assert created.status_code == 201
    carol_id = created.json()["user"]["id"]

    # The caller can hand carol a password (admin-only) and then act as carol.
    reset = await account_client.post(
        f"/api/v1/admin/users/{carol_id}/reset-password",
        headers=headers,
    )
    assert reset.status_code == 200
    carol_password = reset.json()["temporary_password"]
    carol_login = await _login(account_client, "carol", carol_password)
    assert carol_login.status_code == 200
    carol_headers = _bearer(carol_login.json()["access_token"])

    # With a second administrator, self-demotion is allowed and takes effect on
    # the next request because the role is reloaded from the database.
    demote = await account_client.patch(
        f"/api/v1/admin/users/{alice_id}",
        headers=headers,
        json={"role": "user"},
    )
    assert demote.status_code == 200
    assert demote.json()["role"] == "user"
    assert (
        await account_client.get("/api/v1/admin/users", headers=headers)
    ).status_code == 403

    # Carol is now the last active administrator.
    last_admin = await account_client.patch(
        f"/api/v1/admin/users/{carol_id}",
        headers=carol_headers,
        json={"role": "user"},
    )
    assert last_admin.status_code == 409

    self_deactivate = await account_client.post(
        f"/api/v1/admin/users/{carol_id}/deactivate",
        headers=carol_headers,
    )
    assert self_deactivate.status_code == 400

    # Deactivating a normal user (alice) is fine.
    deactivate_alice = await account_client.post(
        f"/api/v1/admin/users/{alice_id}/deactivate",
        headers=carol_headers,
    )
    assert deactivate_alice.status_code == 200
    assert deactivate_alice.json()["is_active"] is False


async def test_deactivation_and_password_reset_revoke_sessions(
    account_client: httpx.AsyncClient,
) -> None:
    alice = await _register(account_client, "alice")
    bob = await _register(account_client, "bob")
    headers = _bearer(alice["access_token"])
    bob_id = bob["user"]["id"]
    bob_headers = _bearer(bob["access_token"])

    assert (
        await account_client.get("/api/v1/auth/me", headers=bob_headers)
    ).status_code == 200

    reset = await account_client.post(
        f"/api/v1/admin/users/{bob_id}/reset-password",
        headers=headers,
    )
    assert reset.status_code == 200, reset.text
    new_password = reset.json()["temporary_password"]
    assert "password_hash" not in reset.text

    # Every session that predates the reset is gone, including other devices.
    assert (
        await account_client.get("/api/v1/auth/me", headers=bob_headers)
    ).status_code == 401
    assert (await _login(account_client, "bob")).status_code == 401
    assert (await _login(account_client, "bob", new_password)).status_code == 200

    # Re-login with the new password to get a session that deactivation revokes.
    relogin = await _login(account_client, "bob", new_password)
    fresh_headers = _bearer(relogin.json()["access_token"])
    assert (
        await account_client.get("/api/v1/auth/me", headers=fresh_headers)
    ).status_code == 200

    deactivated = await account_client.post(
        f"/api/v1/admin/users/{bob_id}/deactivate",
        headers=headers,
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert (
        await account_client.get("/api/v1/auth/me", headers=fresh_headers)
    ).status_code == 401
    assert (await _login(account_client, "bob", new_password)).status_code == 401

    activated = await account_client.post(
        f"/api/v1/admin/users/{bob_id}/activate",
        headers=headers,
    )
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True
    assert (await _login(account_client, "bob", new_password)).status_code == 200


async def test_display_name_can_be_changed_and_cleared(
    account_client: httpx.AsyncClient,
) -> None:
    alice = await _register(account_client, "alice")
    headers = _bearer(alice["access_token"])
    created = await account_client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={"username": "bob", "display_name": "Bob"},
    )
    bob_id = created.json()["user"]["id"]

    renamed = await account_client.patch(
        f"/api/v1/admin/users/{bob_id}",
        headers=headers,
        json={"display_name": "Robert"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["display_name"] == "Robert"

    cleared = await account_client.patch(
        f"/api/v1/admin/users/{bob_id}",
        headers=headers,
        json={"display_name": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["display_name"] is None

    unchanged = await account_client.patch(
        f"/api/v1/admin/users/{bob_id}",
        headers=headers,
        json={},
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["display_name"] is None


async def test_unknown_user_is_404(account_client: httpx.AsyncClient) -> None:
    alice = await _register(account_client, "alice")
    headers = _bearer(alice["access_token"])
    missing = "00000000-0000-0000-0000-000000000000"
    response = await account_client.patch(
        f"/api/v1/admin/users/{missing}",
        headers=headers,
        json={"display_name": "Nobody"},
    )
    assert response.status_code == 404
    response = await account_client.post(
        f"/api/v1/admin/users/{missing}/reset-password", headers=headers
    )
    assert response.status_code == 404


async def test_auth_disabled_local_access_acts_as_admin(
    meeting_client: httpx.AsyncClient,
) -> None:
    # No bearer token and no account table content: the configured local user is
    # the trusted operator, so the gate must let the request through.
    listing = await meeting_client.get("/api/v1/admin/users")
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 0

    # A disabled operator feature answers 503, not 403: the admin gate ran.
    usage = await meeting_client.get("/api/v1/usage/overview")
    assert usage.status_code == 503

    created = await meeting_client.post(
        "/api/v1/admin/users",
        json={"username": "operator", "role": "admin"},
    )
    assert created.status_code == 201, created.text
    operator_id = created.json()["user"]["id"]

    listing = await meeting_client.get("/api/v1/admin/users")
    assert listing.json()["total"] == 1

    # The local caller is not the database account, so deactivating the only
    # active administrator would leave nobody able to operate the console.
    last_admin = await meeting_client.post(
        f"/api/v1/admin/users/{operator_id}/deactivate"
    )
    assert last_admin.status_code == 409


async def test_admin_users_require_a_database(client: httpx.AsyncClient) -> None:
    # ``client`` has accounts and a database disabled: the local operator passes
    # the role gate, but there is no user store to manage.
    response = await client.get("/api/v1/admin/users")
    assert response.status_code == 503
    assert "database-backed runtime" in response.json()["detail"]
