from __future__ import annotations

import json
from pathlib import Path

import pytest
from winnie_index import db, mcp
from winnie_index.config import Config
from winnie_index.scan import scan


@pytest.fixture()
def conn(config: Config):
    connection = db.connect(config.db_path)
    db.ensure_schema(connection)
    scan(connection, config)
    yield connection
    connection.close()


def _call(conn, config, method, params=None, request_id=1):
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    return mcp.handle(conn, config, request)


def test_initialize_echoes_the_requested_version(conn, config):
    response = _call(conn, config, "initialize", {"protocolVersion": "2025-06-18"})
    assert response["result"]["protocolVersion"] == "2025-06-18"
    assert response["result"]["serverInfo"]["name"] == "local-knowledge"
    assert "tools" in response["result"]["capabilities"]


def test_notifications_have_no_response(conn, config):
    assert mcp.handle(conn, config, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_exposes_three_tools(conn, config):
    response = _call(conn, config, "tools/list")
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == ["search_documents", "get_document", "index_status"]


def test_search_tool_returns_citation_text(conn, config):
    response = _call(
        conn,
        config,
        "tools/call",
        {"name": "search_documents", "arguments": {"query": "决定"}},
    )
    text = response["result"]["content"][0]["text"]
    assert "project.md" in text
    assert response["result"]["isError"] is False


def test_get_tool_returns_text(conn, config):
    response = _call(
        conn,
        config,
        "tools/call",
        {"name": "get_document", "arguments": {"path": "project.md"}},
    )
    text = response["result"]["content"][0]["text"]
    assert "Qdrant" in text


def test_missing_query_is_a_tool_error(conn, config):
    response = _call(
        conn, config, "tools/call", {"name": "search_documents", "arguments": {"query": " "}}
    )
    assert response["result"]["isError"] is True


def test_missing_document_is_a_tool_error(conn, config):
    response = _call(
        conn,
        config,
        "tools/call",
        {"name": "get_document", "arguments": {"path": "nope.md"}},
    )
    assert response["result"]["isError"] is True


def test_unknown_method_is_an_error(conn, config):
    response = _call(conn, config, "resources/list")
    assert response["error"]["code"] == -32601


def test_search_returns_empty_message_when_nothing_matches(conn, config):
    response = _call(
        conn,
        config,
        "tools/call",
        {"name": "search_documents", "arguments": {"query": "zzzz-not-present"}},
    )
    assert "No indexed document matched" in response["result"]["content"][0]["text"]
    assert response["result"]["isError"] is False


def test_status_tool_reports_json(conn, config):
    response = _call(conn, config, "tools/call", {"name": "index_status", "arguments": {}})
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["chunks"] > 0
