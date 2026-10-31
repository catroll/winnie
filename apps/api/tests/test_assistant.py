from __future__ import annotations

import httpx
import pytest

from winnie_api.main import create_app
from winnie_api.settings import DatabaseConfig, Settings
from winnie_assistant.config import AssistantConfig, MemoryConfig, SearchConfig, TasksConfig
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

    listing = await assistant_client.get("/api/v1/assistant/conversations")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["title"]


async def test_chat_remember_writes_a_memory_file(
    assistant_client: httpx.AsyncClient,
    assistant_settings: Settings,
) -> None:
    response = await _chat(assistant_client, "记住我喜欢简短回答")
    assert response.status_code == 200, response.text
    assert response.json()["tools_used"] == ["memory_remember"]

    listing = await assistant_client.get("/api/v1/assistant/memory")
    assert listing.status_code == 200, listing.text
    files = listing.json()["files"]
    assert [file["path"] for file in files] == ["preferences.md"]
    assert files[0]["entries"] == 1
    assert files[0]["pinned"] is True

    path = assistant_settings.resolve_assistant_root() / "memory" / "preferences.md"
    text = path.read_text(encoding="utf-8")
    assert "我喜欢简短回答" in text
    assert "<!-- source: chat:" in text

    hits = await assistant_client.get(
        "/api/v1/assistant/memory/search", params={"q": "简短"}
    )
    assert hits.status_code == 200
    assert hits.json()["hits"][0]["text"] == "我喜欢简短回答"
    assert hits.json()["hits"][0]["path"] == "preferences.md"


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
    text = memory.json()["files"][0]["path"]
    assert text == "preferences.md"


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
) -> None:
    status = await assistant_client.get("/api/v1/assistant/status")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["model"] == "fake"
    assert body["search"] == "fake"
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
