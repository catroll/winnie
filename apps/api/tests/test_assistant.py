from __future__ import annotations

import json

import httpx

from winnie_api.main import create_app
from winnie_api.settings import DatabaseConfig, Settings
from winnie_assistant.config import (
    AssistantConfig,
    IndexConfig,
    MemoryConfig,
    SearchConfig,
    TasksConfig,
)
from winnie_core.config import AuthConfig, JobsConfig


async def _chat(
    client: httpx.AsyncClient, message: str, conversation_id: str | None = None
) -> httpx.Response:
    payload: dict[str, object] = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return await client.post("/api/v1/assistant/chat", json=payload)


async def test_chat_records_tool_trace_and_continues_the_conversation(
    assistant_client: httpx.AsyncClient,
    assistant_settings: Settings,
) -> None:
    response = await _chat(assistant_client, "搜索 winnie 项目")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tools_used"] == ["web_search"]
    assert body["reply"].startswith("Fake reply")
    conversation_id = body["conversation_id"]

    detail = await assistant_client.get(
        f"/api/v1/assistant/conversations/{conversation_id}"
    )
    assert detail.status_code == 200, detail.text
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "tool", "assistant"]
    assert messages[1]["name"] == "web_search"
    assert "https://example.com/fake" in messages[1]["text"]

    again = await _chat(assistant_client, "继续", conversation_id)
    assert again.status_code == 200, again.text
    assert again.json()["conversation_id"] == conversation_id
    detail = await assistant_client.get(
        f"/api/v1/assistant/conversations/{conversation_id}"
    )
    assert len(detail.json()["messages"]) == 5

    files = list(
        (assistant_settings.resolve_assistant_root() / "conversations").glob("*/*.md")
    )
    assert len(files) == 1
    stored = files[0].read_text(encoding="utf-8")
    assert "## tool:web_search ·" in stored
    assert "source: \"chat\"" in stored
    assert "channel: \"api\"" in stored

    listing = await assistant_client.get("/api/v1/assistant/conversations")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["title"]
    assert listing.json()["items"][0]["channel"] == "api"


async def test_remember_writes_a_profile_file(
    assistant_client: httpx.AsyncClient,
    assistant_settings: Settings,
) -> None:
    response = await _chat(assistant_client, "记住我喜欢简短回答")
    assert response.status_code == 200, response.text
    assert response.json()["tools_used"] == ["remember"]

    listing = await assistant_client.get("/api/v1/assistant/memory")
    assert listing.status_code == 200, listing.text
    files = listing.json()["files"]
    assert [file["path"] for file in files] == ["profile/preferences.md"]
    assert files[0]["entries"] == 1

    path = assistant_settings.resolve_assistant_root() / "profile" / "preferences.md"
    text = path.read_text(encoding="utf-8")
    assert "我喜欢简短回答" in text
    assert "<!-- source: chat:" in text

    documents = await assistant_client.get(
        "/api/v1/assistant/documents/search", params={"q": "简短"}
    )
    assert documents.status_code == 409


async def test_distillation_is_idempotent(
    assistant_client: httpx.AsyncClient,
) -> None:
    assert (await _chat(assistant_client, "你好")).status_code == 200

    first = await assistant_client.post("/api/v1/assistant/memory/distill")
    assert first.status_code == 200, first.text
    assert first.json() == {"conversations": 1, "facts": 1, "empty": 0}

    second = await assistant_client.post("/api/v1/assistant/memory/distill")
    assert second.status_code == 200
    assert second.json() == {"conversations": 0, "facts": 0, "empty": 0}

    memory = await assistant_client.get("/api/v1/assistant/memory")
    assert memory.json()["entries"] == 1
    assert memory.json()["files"][0]["path"] == "profile/preferences.md"


async def test_tasks_are_created_and_run_through_chat(
    assistant_client: httpx.AsyncClient,
    assistant_settings: Settings,
) -> None:
    response = await _chat(assistant_client, "每天早上7点半提醒我喝水")
    assert response.status_code == 200, response.text
    assert response.json()["tools_used"] == ["schedule_task"]

    listing = await assistant_client.get("/api/v1/assistant/tasks")
    assert listing.status_code == 200, listing.text
    tasks = listing.json()["tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["schedule"] == "daily 07:30"
    assert task["next_run"]
    assert task["last_status"] is None

    stored = assistant_settings.resolve_assistant_root() / "tasks" / "tasks.json"
    assert stored.is_file()
    payload = json.loads(stored.read_text(encoding="utf-8"))
    assert payload["tasks"][0]["id"] == task["id"]
    assert payload["tasks"][0]["prompt"] == "每天早上7点半提醒我喝水"

    run = await assistant_client.post(f"/api/v1/assistant/tasks/{task['id']}/run")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "ok"
    conversation_id = run.json()["conversation_id"]
    assert conversation_id

    detail = await assistant_client.get(
        f"/api/v1/assistant/conversations/{conversation_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["source"] == "task"
    assert detail.json()["channel"] == "task"

    blocked = await _chat(assistant_client, "继续", conversation_id)
    assert blocked.status_code == 409, blocked.text

    runs = (
        assistant_settings.resolve_assistant_root() / "tasks" / "runs.jsonl"
    ).read_text(encoding="utf-8")
    assert json.loads(runs.splitlines()[0])["status"] == "ok"

    cancelled = await _chat(assistant_client, f"取消任务 {task['id']}")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["tools_used"] == ["cancel_task"]
    remaining = await assistant_client.get("/api/v1/assistant/tasks")
    assert remaining.json()["tasks"] == []
    payload = json.loads(stored.read_text(encoding="utf-8"))
    assert payload["tasks"] == []


async def test_conversation_channels_are_recorded_and_filtered(
    assistant_client: httpx.AsyncClient,
) -> None:
    admin = await assistant_client.post(
        "/api/v1/assistant/chat",
        json={"message": "from the console", "channel": "admin"},
    )
    assert admin.status_code == 200, admin.text
    web = await assistant_client.post(
        "/api/v1/assistant/chat",
        json={"message": "from the phone", "channel": "web"},
    )
    assert web.status_code == 200, web.text

    listing = await assistant_client.get(
        "/api/v1/assistant/conversations", params={"channel": "admin"}
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["channel"] == "admin"
    assert body["items"][0]["id"] == admin.json()["conversation_id"]

    everything = await assistant_client.get("/api/v1/assistant/conversations")
    assert everything.json()["total"] == 2
    assert {item["channel"] for item in everything.json()["items"]} == {
        "admin",
        "web",
    }

    detail = await assistant_client.get(
        f"/api/v1/assistant/conversations/{admin.json()['conversation_id']}"
    )
    assert detail.json()["channel"] == "admin"

    rejected = await assistant_client.post(
        "/api/v1/assistant/chat",
        json={"message": "spoofed", "channel": "task"},
    )
    assert rejected.status_code == 422


async def test_documents_search_uses_the_local_index(tmp_path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "index.invalid" and request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "query": "部署",
                    "results": [
                        {
                            "path": "Winnie/profile/preferences.md",
                            "root": "Documents/Mine",
                            "heading": "Preferences",
                            "start_line": 3,
                            "end_line": 3,
                            "snippet": "先给结论，再展开。",
                            "score": -1.5,
                            "mtime": "2026-11-01T00:00:00+00:00",
                            "also_at": [],
                        }
                    ],
                },
            )
        return httpx.Response(500)

    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        assistant=AssistantConfig(
            enabled=True,
            root=str(tmp_path / "assistant"),
            model="fake",
            search=SearchConfig(provider="fake"),
            index=IndexConfig(enabled=True, url="http://index.invalid:8790"),
            memory=MemoryConfig(auto_distill=False),
            tasks=TasksConfig(enabled=False),
        ),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(settings=settings, http=http)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/v1/assistant/documents/search", params={"q": "部署"}
                )
                assert response.status_code == 200, response.text
                hits = response.json()["hits"]
                assert hits[0]["path"] == "Winnie/profile/preferences.md"
                assert hits[0]["start_line"] == 3
                assert seen[0].url.path == "/search"
                assert json.loads(seen[0].content)["query"] == "部署"
    finally:
        await http.aclose()


async def test_unknown_and_malformed_conversation_ids_are_404(
    assistant_client: httpx.AsyncClient,
) -> None:
    for conversation_id in ("not-an-id", "20260101T000000Z-zzzzzz", "../../etc/passwd"):
        response = await assistant_client.get(
            f"/api/v1/assistant/conversations/{conversation_id}"
        )
        assert response.status_code == 404, conversation_id
    response = await _chat(
        assistant_client, "hi", conversation_id="20260101T000000Z-abcdef"
    )
    assert response.status_code == 404


async def test_status_and_chat_page(
    assistant_client: httpx.AsyncClient,
    assistant_settings: Settings,
) -> None:
    status = await assistant_client.get("/api/v1/assistant/status")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["model"] == "fake"
    assert body["search"] == "fake"
    assert body["index"]["enabled"] is False
    assert body["root"] == str(assistant_settings.resolve_assistant_root())
    assert body["memory"]["enabled"] is True
    assert body["scheduler"]["tasks"] == 0

    page = await assistant_client.get("/api/v1/assistant/ui")
    assert page.status_code == 200
    assert "Winnie" in page.text
    assert "assistant/chat" not in page.text


async def test_assistant_routes_require_a_token_when_auth_is_enabled(
    tmp_path,
) -> None:
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        auth=AuthConfig(enabled=True, allow_registration=False),
        assistant=AssistantConfig(
            enabled=True,
            root=str(tmp_path / "assistant"),
            model="fake",
            search=SearchConfig(provider="fake"),
            index=IndexConfig(enabled=False),
            memory=MemoryConfig(auto_distill=False),
            tasks=TasksConfig(enabled=False),
        ),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/assistant/chat", json={"message": "hello"}
            )
            assert response.status_code == 401
            page = await client.get("/api/v1/assistant/ui")
            assert page.status_code == 200
    await http.aclose()


async def test_a_task_run_cannot_create_tasks(
    assistant_client: httpx.AsyncClient,
) -> None:
    created = await _chat(assistant_client, "每天早上7点半提醒我喝水")
    assert created.status_code == 200
    tasks = (await assistant_client.get("/api/v1/assistant/tasks")).json()["tasks"]
    assert len(tasks) == 1
    task = tasks[0]

    run = await assistant_client.post(f"/api/v1/assistant/tasks/{task['id']}/run")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "ok"

    after = (await assistant_client.get("/api/v1/assistant/tasks")).json()["tasks"]
    assert [item["id"] for item in after] == [task["id"]]
