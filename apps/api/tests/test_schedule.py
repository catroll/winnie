"""The console's schedule surface: read a week, add, update and delete events.

Every test drives the real router over ASGI against a temporary schedule tree,
so the assertions are about HTTP status, the refreshed week payload and the
bytes on disk.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from winnie_api.main import create_app
from winnie_api.settings import ScheduleConfig, Settings

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

WEEK_TEXT = """# 周安排 05 - 11

## 工作事项

-   [ ] **`项目名称`** 开发内容描述

### 10/05 周一

-   [ ] `10/05` [周一] [09:30~10:30] 值班巡检
-   [x] `10/05` 已完成事项

### 10/06 周二

### 10/07 周三

### 10/08 周四

### 10/09 周五

### 10/10 周六

### 10/11 周日

## 周末总结

### 本周工作总结

...

<!-- ############################################################ -->

[重点]: https://img.shields.io/badge/-⏺-red "重点"
"""


def week_stem(monday: date) -> str:
    return f"{monday:%Y%m}-{monday:%d}-{monday + timedelta(days=6):%d}"


def write_week(root: Path, monday: date, text: str) -> Path:
    directory = root / "work" / f"{monday.year:04d}" / f"Q{(monday.month + 2) // 3}" / f"{monday.month:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{week_stem(monday)}.md"
    path.write_text(text, encoding="utf-8")
    return path


def current_week_text(day: date) -> str:
    monday = day - timedelta(days=day.weekday())
    headings = "\n\n".join(
        f"### {monday + timedelta(days=index):%m/%d} {WEEKDAYS[index]}" for index in range(7)
    )
    return (
        f"# 周安排 {monday:%d} - {monday + timedelta(days=6):%d}\n\n"
        "## 工作事项\n\n-   [ ] 本周项目事项\n\n"
        f"{headings}\n\n"
        f"-   [ ] `{day:%m/%d}` 今天的事项\n"
    )


@pytest.fixture
def schedule_root(tmp_path: Path) -> Path:
    """A tree with the fixed test week and a week that contains today."""
    root = tmp_path / "WLB"
    write_week(root, date(2026, 10, 5), WEEK_TEXT)
    today = date.today()
    write_week(root, today - timedelta(days=today.weekday()), current_week_text(today))
    return root


@pytest.fixture
def week_path(schedule_root: Path) -> Path:
    return schedule_root / "work" / "2026" / "Q4" / "10" / "202610-05-11.md"


def make_settings(root: Path) -> Settings:
    return Settings(
        log_root="/tmp/winnie-schedule-logs",
        routes={},
        schedule=ScheduleConfig(enabled=True, root=str(root)),
    )


async def open_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest_asyncio.fixture
async def client(schedule_root: Path) -> AsyncIterator[httpx.AsyncClient]:
    async for yielded in open_client(make_settings(schedule_root)):
        yield yielded


@pytest_asyncio.fixture
async def legacy_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    root = tmp_path / "OLD"
    directory = root / "work" / "2019" / "04"
    directory.mkdir(parents=True)
    (directory / "201904-01-07.md").write_text(
        "# 周安排 01 - 07\n\n## 开发\n\n-   [ ] 旧事项\n", encoding="utf-8"
    )
    async for yielded in open_client(make_settings(root)):
        yield yielded


@pytest_asyncio.fixture
async def broken_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    root = tmp_path / "EMPTY"
    root.mkdir()
    async for yielded in open_client(make_settings(root)):
        yield yielded


@pytest.mark.asyncio
async def test_week_returns_days_and_events(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/schedule/week", params={"date": "2026-10-05"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["stem"] == "202610-05-11"
    assert payload["layout"] == "canonical"
    assert payload["editable"] is True
    assert payload["week"]["label"] == "2026-10A"
    assert [day["date"] for day in payload["days"]][0] == "2026-10-05"
    first = payload["days"][0]["events"][0]
    assert first["id"] == "202610-05-11#10/05#1"
    assert first["start"] == "09:30"
    assert first["end"] == "10:30"
    assert first["text"] == "值班巡检"
    assert payload["days"][0]["events"][1]["done"] is True
    assert payload["sections"][0]["title"] == "工作事项"
    assert payload["sections"][0]["events"][0]["scope"] == "week"


@pytest.mark.asyncio
async def test_week_defaults_to_today(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/schedule/week")
    assert response.status_code == 200
    payload = response.json()
    today = date.today()
    assert payload["week"]["monday"] <= today.isoformat() <= payload["week"]["sunday"]
    assert payload["today"] == today.isoformat()


@pytest.mark.asyncio
async def test_create_event_updates_the_file(
    client: httpx.AsyncClient, week_path: Path
) -> None:
    response = await client.post(
        "/api/v1/schedule/events",
        json={
            "date": "2026-10-06",
            "text": "评审邮件模板",
            "time": "14:00~15:00",
            "weekday": True,
            "tags": ["newapi"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["changed"] is True
    assert payload["event"]["id"] == "202610-05-11#10/06#1"
    assert payload["event"]["text"] == "评审邮件模板"
    assert "-   [ ] `10/06` [周二] [14:00~15:00] [newapi] 评审邮件模板" in week_path.read_text(
        encoding="utf-8"
    )
    # The refreshed week already contains the new event.
    assert payload["week"]["days"][1]["events"][0]["id"] == "202610-05-11#10/06#1"


@pytest.mark.asyncio
async def test_create_week_scoped_event(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/schedule/events",
        json={"date": "2026-10-05", "text": "季度目标对齐", "week_scope": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["event"]["scope"] == "week"
    assert payload["event"]["date"] is None


@pytest.mark.asyncio
async def test_update_checks_the_event(client: httpx.AsyncClient) -> None:
    response = await client.patch(
        "/api/v1/schedule/events",
        json={"week": "2026-10-05", "id": "202610-05-11#10/05#1", "done": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["event"]["done"] is True
    assert payload["event"]["text"] == "值班巡检"


@pytest.mark.asyncio
async def test_update_moves_the_event_to_another_day(client: httpx.AsyncClient) -> None:
    response = await client.patch(
        "/api/v1/schedule/events",
        json={"week": "2026-10-05", "id": "202610-05-11#10/05#1", "move_to": "10/07"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["event"]["date"] == "10/07"
    assert payload["week"]["days"][2]["events"][0]["text"] == "值班巡检"


@pytest.mark.asyncio
async def test_update_clears_the_time(client: httpx.AsyncClient) -> None:
    response = await client.patch(
        "/api/v1/schedule/events",
        json={"week": "2026-10-05", "id": "202610-05-11#10/05#1", "time": ""},
    )
    assert response.status_code == 200
    assert response.json()["event"]["start"] is None


@pytest.mark.asyncio
async def test_delete_restores_the_original_file(
    client: httpx.AsyncClient, week_path: Path
) -> None:
    created = await client.post(
        "/api/v1/schedule/events", json={"date": "2026-10-07", "text": "临时事项"}
    )
    event_id = created.json()["event"]["id"]
    response = await client.delete(
        "/api/v1/schedule/events", params={"week": "2026-10-07", "id": event_id}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["removed"] == ["-   [ ] `10/07` 临时事项"]
    assert "临时事项" not in week_path.read_text(encoding="utf-8")
    assert week_path.read_text(encoding="utf-8").strip() == WEEK_TEXT.strip()


@pytest.mark.asyncio
async def test_a_bad_time_is_rejected_before_writing(
    client: httpx.AsyncClient, week_path: Path
) -> None:
    before = week_path.read_bytes()
    response = await client.post(
        "/api/v1/schedule/events",
        json={"date": "2026-10-06", "text": "x", "time": "10:00~09:00"},
    )
    assert response.status_code == 400
    assert "ends before it starts" in response.json()["detail"]
    assert week_path.read_bytes() == before


@pytest.mark.asyncio
async def test_unknown_event_is_404(client: httpx.AsyncClient) -> None:
    response = await client.delete(
        "/api/v1/schedule/events",
        params={"week": "2026-10-05", "id": "202610-05-11#10/05#99"},
    )
    assert response.status_code == 404
    assert "no event" in response.json()["detail"]


@pytest.mark.asyncio
async def test_week_without_a_file_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/schedule/week", params={"date": "2027-03-01"})
    assert response.status_code == 404
    assert "weekly generator" in response.json()["detail"]


@pytest.mark.asyncio
async def test_legacy_week_is_readable_but_not_writable(
    legacy_client: httpx.AsyncClient,
) -> None:
    read = await legacy_client.get("/api/v1/schedule/week", params={"date": "2019-04-02"})
    assert read.status_code == 200
    payload = read.json()
    assert payload["layout"] == "legacy"
    assert payload["editable"] is False
    assert payload["sections"][0]["events"][0]["text"] == "旧事项"

    write = await legacy_client.post(
        "/api/v1/schedule/events", json={"date": "2019-04-02", "text": "x"}
    )
    assert write.status_code == 409
    assert "read-only" in write.json()["detail"]


@pytest.mark.asyncio
async def test_a_missing_tree_is_reported_as_unavailable(
    broken_client: httpx.AsyncClient,
) -> None:
    response = await broken_client.get("/api/v1/schedule/week")
    assert response.status_code == 503
    assert "work/" in response.json()["detail"]


@pytest.mark.asyncio
async def test_schedule_is_absent_when_disabled(tmp_path: Path) -> None:
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        schedule=ScheduleConfig(enabled=False, root=str(tmp_path)),
    )
    async for client in open_client(settings):
        response = await client.get("/api/v1/schedule/week")
        assert response.status_code == 404
