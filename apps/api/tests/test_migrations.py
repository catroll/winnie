from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config

from winnie_api.main import create_app
from winnie_api.settings import load_settings

_API_ROOT = Path(__file__).resolve().parents[1]


def _config_yaml(tmp_path: Path, db_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            [
                "auth:",
                "  enabled: true",
                "  allow_registration: true",
                "database:",
                f"  url: sqlite+aiosqlite:///{db_path}",
                "  auto_create_tables: false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def migrated_database(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "app.db"
    config_path = _config_yaml(tmp_path, db_path)
    monkeypatch.setenv("WINNIE_CONFIG", str(config_path))
    config = Config(str(_API_ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    return config_path, config, db_path


def _tables(db_path: Path) -> set[str]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def test_upgrade_creates_expected_schema(migrated_database) -> None:
    _, config, db_path = migrated_database
    command.check(config)

    tables = _tables(db_path)
    assert {
        "alembic_version",
        "auth_sessions",
        "jobs",
        "meetings",
        "minutes",
        "segments",
        "usage_records",
        "users",
    } <= tables


def test_downgrade_removes_application_tables(migrated_database) -> None:
    _, config, db_path = migrated_database
    command.downgrade(config, "base")
    assert _tables(db_path).isdisjoint(
        {"auth_sessions", "jobs", "meetings", "minutes", "segments", "usage_records", "users"}
    )


async def test_app_uses_migrated_schema_without_create_all(migrated_database) -> None:
    config_path, _, _ = migrated_database
    app = create_app(settings=load_settings(config_path))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/auth/register",
                json={"username": "migrated", "password": "correct horse battery"},
            )
            assert response.status_code == 201, response.text
