from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from winnie_index import db, httpapi, skill
from winnie_index.config import Config
from winnie_index.scan import scan


@pytest.fixture()
def server(config: Config):
    connection = db.connect(config.db_path)
    db.ensure_schema(connection)
    scan(connection, config)
    connection.close()

    httpd = httpapi.create_server(config, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read())


def _post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def test_healthz(server: str):
    assert _get(server + "/healthz")["status"] == "ok"


def test_search_endpoint(server: str):
    payload = _post(server + "/search", {"query": "Qdrant"})
    assert payload["results"]
    assert payload["results"][0]["path"] in {"project.md", "copy.md"}


def test_doc_endpoint_returns_line_window(server: str):
    payload = _get(server + "/doc?path=project.md&from=1&lines=1")
    assert payload["text"].startswith("# 项目记录")
    assert payload["fresh"] is True


def test_doc_endpoint_rejects_unknown_path(server: str):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server + "/doc?path=missing.md")
    assert exc.value.code == 404


def test_mcp_initialize_over_http(server: str):
    response = _post(
        server + "/mcp",
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
    )
    assert response["result"]["protocolVersion"] == "2025-06-18"


def test_mcp_tools_list_over_http(server: str):
    response = _post(server + "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(response["result"]["tools"]) == 3


def test_mcp_notification_returns_202(server: str):
    request = urllib.request.Request(
        server + "/mcp",
        data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 202


def test_status_endpoint(server: str):
    payload = _get(server + "/status")
    assert payload["chunks"] > 0


def test_bad_json_is_400(server: str):
    request = urllib.request.Request(
        server + "/search",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=10)
    assert exc.value.code == 400


def test_skill_install_and_drift(config: Config, tmp_path: Path):
    target = tmp_path / "skill"
    path = skill.install(config, target)
    assert path.read_text(encoding="utf-8").startswith("---")
    ok, _ = skill.check(config, target)
    assert ok
    path.write_text("changed", encoding="utf-8")
    ok, _ = skill.check(config, target)
    assert not ok
