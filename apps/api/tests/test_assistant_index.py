from __future__ import annotations

import json

import httpx
import pytest

from winnie_assistant.index_client import IndexClient, IndexUnavailable


def _client(handler, **kwargs):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return IndexClient(url="http://index.test:8790", http=http, **kwargs), http


async def test_search_sends_the_query_and_parses_hits() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
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
                        "end_line": 4,
                        "snippet": "先给结论。",
                        "score": -2.0,
                        "mtime": "2026-11-01T00:00:00+00:00",
                        "also_at": ["Winnie/old.md"],
                    }
                ],
            },
        )

    client, http = _client(handler)
    try:
        hits = await client.search("部署", limit=3)
    finally:
        await http.aclose()

    assert json.loads(seen[0].content) == {"query": "部署", "limit": 3}
    assert seen[0].url.path == "/search"
    assert hits[0].path == "Winnie/profile/preferences.md"
    assert hits[0].heading == "Preferences"
    assert hits[0].start_line == 3
    assert hits[0].also_at == ("Winnie/old.md",)


async def test_token_from_the_environment_is_sent(monkeypatch) -> None:
    headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        headers.update(request.headers)
        return httpx.Response(200, json={"query": "x", "results": []})

    monkeypatch.setenv("WINNIE_INDEX_TOKEN", "secret")
    client, http = _client(handler)
    try:
        assert await client.search("x") == []
    finally:
        await http.aclose()
    assert headers["authorization"] == "Bearer secret"


async def test_http_error_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "catalog locked"})

    client, http = _client(handler)
    try:
        with pytest.raises(IndexUnavailable, match="HTTP 500.*catalog locked"):
            await client.search("x")
    finally:
        await http.aclose()


async def test_transport_error_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client, http = _client(handler)
    try:
        with pytest.raises(IndexUnavailable, match="unreachable"):
            await client.search("x")
    finally:
        await http.aclose()


async def test_read_and_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/doc":
            assert request.url.params["path"] == "Winnie/profile/preferences.md"
            return httpx.Response(
                200,
                json={
                    "path": "Winnie/profile/preferences.md",
                    "root": "Documents/Mine",
                    "from_line": 1,
                    "total_lines": 4,
                    "fresh": True,
                    "text": "# Preferences\n- 先给结论\n",
                },
            )
        return httpx.Response(
            200, json={"documents": {"indexed": 1005}, "chunks": 35205}
        )

    client, http = _client(handler)
    try:
        document = await client.read("Winnie/profile/preferences.md")
        status = await client.status()
    finally:
        await http.aclose()
    assert document["text"].startswith("# Preferences")
    assert status["documents"]["indexed"] == 1005
