from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from winnie_api.settings import (
    DatabaseConfig,
    Settings,
    load_settings,
    refuse_live_database_under_pytest,
)


def test_relative_sqlite_url_becomes_absolute() -> None:
    settings = Settings(
        database=DatabaseConfig(url="sqlite+aiosqlite:///../../data/winnie.db")
    )
    resolved = settings.resolve_database_url()
    assert resolved.startswith("sqlite+aiosqlite:////")
    assert resolved.endswith("/data/winnie.db")


def test_absolute_and_server_urls_are_unchanged() -> None:
    absolute = "sqlite+aiosqlite:////tmp/winnie.db"
    assert Settings(database=DatabaseConfig(url=absolute)).resolve_database_url() == absolute

    postgres = "postgresql+asyncpg://user:pass@db:5432/winnie"
    assert Settings(database=DatabaseConfig(url=postgres)).resolve_database_url() == postgres


def test_meeting_paths_are_absolute() -> None:
    settings = Settings()
    assert settings.resolve_meetings_root().is_absolute()
    assert settings.resolve_notes_root().is_absolute()
    assert settings.resolve_notes_root().as_posix().endswith("data/kb/meetings")


def test_nested_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(meetings={"enabled": True, "typo": 1})


def test_a_test_that_forgets_its_database_is_refused() -> None:
    """A fixture that forgets ``database=`` used to reach the owner's data.

    ``test_phone_geocode`` synced a fake Pixel location into ``data/winnie.db``
    that way. The engine factory now refuses, and the message names the fix.
    """
    resolved = Settings().resolve_database_url()
    assert resolved.endswith("/data/winnie.db")
    with pytest.raises(RuntimeError, match="live database"):
        refuse_live_database_under_pytest(resolved)


def test_the_live_database_guard_is_inert_outside_pytest(monkeypatch) -> None:
    """The running server may of course use the real database."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    refuse_live_database_under_pytest(Settings().resolve_database_url())


def test_local_overlay_wins(tmp_path: Path) -> None:
    primary = tmp_path / "config.yaml"
    primary.write_text(
        "meetings:\n  enabled: false\n  max_upload_mb: 100\n",
        encoding="utf-8",
    )
    (tmp_path / "config.local.yaml").write_text(
        "meetings:\n  enabled: true\n  max_upload_mb: 5\n",
        encoding="utf-8",
    )
    settings = load_settings(primary)
    assert settings.meetings.enabled is True
    assert settings.meetings.max_upload_mb == 5
