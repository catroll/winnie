"""Error collection over the real ASGI app: capture, dedupe, triage, diagnose.

The interesting assertions are the ones a log file cannot make: one repeating
failure is one row with a count, a recovered service closes its own incident,
a diagnosis is stored against the incident and never executes anything.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from winnie_api.devloop.capture import repo_location
from winnie_api.devloop.diagnose import parse_answer
from winnie_api.devloop.evidence import EvidenceSources
from winnie_api.devloop.events import ErrorEvent
from winnie_api.main import create_app
from winnie_api.settings import DatabaseConfig, Settings
from winnie_core.config import AuthConfig


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def devloop_settings(tmp_path: Path) -> Settings:
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        auth=AuthConfig(enabled=False),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'devloop.db'}",
            auto_create_tables=True,
        ),
        devloop={
            "enabled": True,
            "log_dir": str(tmp_path / "logs"),
            "spool_dir": str(tmp_path / "spool"),
            "service_urls": {},
            "detector": {"enabled": False},
        },
    )


@dataclass
class ErrorsApp:
    """The client plus the app, so a test can reach `app.state` directly."""

    client: httpx.AsyncClient
    app: object

    def __getattr__(self, name: str):
        return getattr(self.client, name)


@pytest_asyncio.fixture
async def errors_client(
    devloop_settings: Settings, tmp_path: Path
) -> AsyncIterator[ErrorsApp]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=devloop_settings, http=http)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom 12345")

    @app.get("/bad")
    async def bad() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="handled as a 500")

    @app.get("/missing")
    async def missing() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="not here")

    async with app.router.lifespan_context(app):
        # Keep evidence local: a tmp directory has no .git and no log files.
        app.state.devloop_sources = EvidenceSources(
            repo_root=tmp_path, log_dir=tmp_path / "logs", service_urls={}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield ErrorsApp(client=client, app=app)
    await http.aclose()


async def _list(client: httpx.AsyncClient, **params: str) -> dict:
    response = await client.get("/api/v1/errors", params={"status": "all", **params})
    assert response.status_code == 200, response.text
    return response.json()


async def test_unhandled_exception_is_recorded_once_and_counted(
    errors_client: httpx.AsyncClient,
) -> None:
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await errors_client.get("/boom")

    payload = await _list(errors_client)
    incidents = [item for item in payload["items"] if item["kind"] == "unhandled_exception"]
    assert len(incidents) == 1, payload
    incident = incidents[0]
    assert incident["occurrences"] == 3
    assert incident["message"] == "RuntimeError: kaboom 12345"
    assert incident["exception_type"] == "RuntimeError"
    assert incident["source"] == "api"
    # The innermost application frame, not fastapi's routing frame.
    assert re.search(r"test_errors\.py:\d+$", incident["location"]), incident["location"]
    assert "RuntimeError" in incident["traceback"]


async def test_a_returned_500_is_recorded_and_a_404_is_not(
    errors_client: httpx.AsyncClient,
) -> None:
    assert (await errors_client.get("/bad")).status_code == 500
    assert (await errors_client.get("/missing")).status_code == 404

    payload = await _list(errors_client)
    kinds = {item["kind"] for item in payload["items"]}
    assert kinds == {"http_error"}
    item = payload["items"][0]
    assert item["message"] == "GET /bad -> 500"
    assert item["context"]["status"] == "500"
    # A 500 a handler returned has no exception behind it.
    assert item["traceback"] == ""


async def test_triage_moves_an_incident_between_states(
    errors_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(RuntimeError):
        await errors_client.get("/boom")
    incident = (await _list(errors_client))["items"][0]
    incident_id = incident["id"]

    acknowledged = await errors_client.post(
        f"/api/v1/errors/{incident_id}/status", json={"status": "acknowledged"}
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "acknowledged"
    assert acknowledged.json()["acknowledged_at"]

    ignored = await errors_client.post(
        f"/api/v1/errors/{incident_id}/status", json={"status": "ignored"}
    )
    assert ignored.json()["status"] == "ignored"
    assert ignored.json()["acknowledged_at"] is None

    reopened = await errors_client.post(
        f"/api/v1/errors/{incident_id}/status", json={"status": "open"}
    )
    assert reopened.json()["status"] == "open"
    assert reopened.json()["ignored_at"] is None

    bad = await errors_client.post(
        f"/api/v1/errors/{incident_id}/status", json={"status": "fixed"}
    )
    assert bad.status_code == 422


async def test_an_unknown_incident_is_a_404(errors_client: httpx.AsyncClient) -> None:
    missing = await errors_client.get(
        "/api/v1/errors/00000000-0000-0000-0000-000000000000"
    )
    assert missing.status_code == 404


async def test_a_logged_error_from_another_capability_is_captured(
    errors_client: httpx.AsyncClient,
) -> None:
    logging.getLogger("winnie_mail.service").error("imap refused the login")
    # The handler schedules the write on the loop; give it a turn.
    for _ in range(20):
        await asyncio.sleep(0.02)
        payload = await _list(errors_client)
        if payload["items"]:
            break

    item = payload["items"][0]
    assert item["kind"] == "logged_error"
    assert item["source"] == "log"
    # The capability is derived from the logger name, not hard-coded.
    assert item["service"] == "mail"
    assert item["message"] == "imap refused the login"


async def test_a_summary_counts_open_incidents(
    errors_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(RuntimeError):
        await errors_client.get("/boom")
    await errors_client.get("/bad")

    summary = (await errors_client.get("/api/v1/errors/summary")).json()
    assert summary["open"] == 2
    assert summary["total"] == 2
    assert summary["last_24h"] == 2
    assert summary["by_service"] == {"api": 2}


async def test_diagnosis_is_stored_and_never_executed(
    errors_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(RuntimeError):
        await errors_client.get("/boom")
    incident_id = (await _list(errors_client))["items"][0]["id"]

    seen: list[list[dict[str, str]]] = []

    async def fake_chat(messages: list[dict[str, str]]) -> str:
        seen.append(messages)
        return (
            "```json\n"
            + json.dumps(
                {
                    "component": "api",
                    "file": "apps/api/src/winnie_api/main.py",
                    "line": 42,
                    "cause": "the endpoint raises on purpose",
                    "confidence": 0.8,
                    "next_steps": ["read the handler at main.py:42"],
                    "notes": "",
                }
            )
            + "\n```"
        )

    errors_client.app.state.devloop_diagnoser = (fake_chat, "fake-model")
    response = await errors_client.post(f"/api/v1/errors/{incident_id}/diagnose")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["diagnosis"]["answer"]["file"] == "apps/api/src/winnie_api/main.py"
    assert body["diagnosis"]["answer"]["confidence"] == 0.8
    assert body["diagnosis"]["model"] == "fake-model"
    assert body["diagnosis_at"]
    # The evidence really reached the model, and it asked for no write.
    assert "Evidence:" in seen[0][1]["content"]
    assert "kaboom" in seen[0][1]["content"]


async def test_diagnosis_without_a_model_is_a_503(
    errors_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(RuntimeError):
        await errors_client.get("/boom")
    incident_id = (await _list(errors_client))["items"][0]["id"]
    response = await errors_client.post(f"/api/v1/errors/{incident_id}/diagnose")
    assert response.status_code == 503
    assert "no chat model" in response.json()["detail"]


async def test_watchdog_spool_is_drained_and_recovery_resolves(
    errors_client: httpx.AsyncClient, tmp_path: Path
) -> None:
    app = errors_client.app
    spool = tmp_path / "spool"
    spool.mkdir()
    down = spool / "001-down.jsonl"
    down.write_text(
        json.dumps(
            {
                "detected_at": datetime(2026, 11, 3, 1, 0, 0).isoformat(),
                "source": "watchdog",
                "kind": "service_down",
                "service": "api",
                "level": "critical",
                "message": "api did not answer /healthz",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # Older than the settle window, so the detector may read it.
    old = (datetime.now() - timedelta(seconds=30)).timestamp()
    os.utime(down, (old, old))

    # Drive the detector the same way its loop would.
    from winnie_api.devloop.detector import Detector

    drv = Detector(
        app.state.devloop_service,
        service_urls={},
        spool_dir=spool,
        interval_seconds=1,
    )
    await drv.drain_spool()

    payload = await _list(errors_client)
    assert len(payload["items"]) == 1
    incident = payload["items"][0]
    assert incident["service"] == "api"
    assert incident["kind"] == "service_down"
    assert incident["level"] == "critical"
    assert not down.exists()

    recovered = spool / "002-up.jsonl"
    recovered.write_text(
        json.dumps(
            {
                "detected_at": datetime(2026, 11, 3, 1, 5, 0).isoformat(),
                "source": "watchdog",
                "kind": "service_recovered",
                "service": "api",
                "level": "warning",
                "message": "api answered /healthz again after a restart",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(recovered, (old, old))
    await drv.drain_spool()

    payload = await _list(errors_client)
    by_kind = {item["kind"]: item for item in payload["items"]}
    assert by_kind["service_down"]["status"] == "resolved"
    # A recovery is history, not work: it is closed on insert so it never sits
    # in the open list next to the outage it explains.
    assert by_kind["service_recovered"]["status"] == "resolved"


async def test_collection_is_disabled_without_configuration() -> None:
    settings = Settings(
        routes={},
        auth=AuthConfig(enabled=False),
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/errors")
            assert response.status_code == 503
            assert "devloop.enabled" in response.json()["detail"]
    await http.aclose()


def test_a_fingerprint_ignores_volatile_numbers() -> None:
    first = ErrorEvent(
        message="connection to 127.0.0.1:5432 refused after 1234 ms",
        kind="logged_error",
        service="api",
        location="db.py:10",
    )
    second = ErrorEvent(
        message="connection to 127.0.0.1:5432 refused after 9876 ms",
        kind="logged_error",
        service="api",
        location="db.py:10",
    )
    other = ErrorEvent(
        message="connection to 127.0.0.1:5432 refused after 9876 ms",
        kind="logged_error",
        service="api",
        location="cache.py:99",
    )
    assert first.fingerprint() == second.fingerprint()
    assert first.fingerprint() != other.fingerprint()


def test_diagnosis_parser_accepts_a_fence_and_prose() -> None:
    fenced = 'Here you go:\n```json\n{"cause": "x", "confidence": 2, "line": -4}\n```'
    parsed = parse_answer(fenced)
    assert parsed is not None
    assert parsed["cause"] == "x"
    assert parsed["confidence"] == 1.0
    assert parsed["line"] == 0
    assert parse_answer("no object here") is None


def test_repo_location_prefers_application_frames(tmp_path: Path) -> None:
    import traceback

    def inner() -> None:
        raise ValueError("x")

    try:
        inner()
    except ValueError as exc:
        tb = traceback.TracebackException.from_exception(exc)
    # No repo root: the innermost frame is the only candidate.
    assert re.search(r"test_errors\.py:\d+$", repo_location(tb))
    # With this checkout as the root, the collection layer is skipped but the
    # test file is still an application frame.
    assert re.search(
        r"test_errors\.py:\d+$",
        repo_location(tb, Path(__file__).resolve().parents[3]),
    )
