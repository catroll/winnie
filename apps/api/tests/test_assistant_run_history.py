from __future__ import annotations

import json

import httpx

from winnie_api.settings import Settings


def _line(task_id: str, status: str, started: str, finished: str) -> str:
    return json.dumps(
        {
            "task_id": task_id,
            "status": status,
            "started_at": started,
            "finished_at": finished,
            "conversation_id": f"conv-{task_id}-{status}",
            "error": "",
            "delivery_error": "",
        }
    )


async def test_task_run_history_endpoint_pages_and_filters(
    assistant_client: httpx.AsyncClient,
    assistant_settings: Settings,
) -> None:
    path = assistant_settings.resolve_assistant_root() / "tasks" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                _line(
                    "brief",
                    "error",
                    "2026-11-01T07:30:00+08:00",
                    "2026-11-01T07:30:05+08:00",
                ),
                "not a run record",
                _line(
                    "brief",
                    "ok",
                    "2026-11-02T07:30:00+08:00",
                    "2026-11-02T07:30:03+08:00",
                ),
                _line(
                    "other",
                    "skipped",
                    "2026-11-02T09:00:00+08:00",
                    "2026-11-02T09:00:00+08:00",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    response = await assistant_client.get("/api/v1/assistant/tasks/runs")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["offset"] == 0
    assert body["limit"] == 50
    assert body["skipped"] == 1
    assert [run["task_id"] for run in body["runs"]] == ["other", "brief", "brief"]
    assert body["runs"][1]["conversation_id"] == "conv-brief-ok"

    page = await assistant_client.get(
        "/api/v1/assistant/tasks/runs",
        params={"task_id": "brief", "limit": 1, "offset": 1},
    )
    assert page.status_code == 200, page.text
    page_body = page.json()
    assert page_body["total"] == 2
    assert page_body["limit"] == 1
    assert page_body["offset"] == 1
    assert [run["status"] for run in page_body["runs"]] == ["error"]
