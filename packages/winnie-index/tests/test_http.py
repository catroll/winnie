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


def test_admin_search_returns_chunk_detail(server: str):
    payload = _post(server + "/admin/search", {"query": "Qdrant"})
    assert payload["expression"] == '"qdrant"'
    assert payload["returned"] >= 2
    assert {hit["path"] for hit in payload["hits"]} == {"project.md", "copy.md"}
    assert payload["files"]


def test_admin_search_without_a_query_is_400(server: str):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(server + "/admin/search", {"query": " "})
    assert exc.value.code == 400


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


def _put(url: str, payload: dict, headers: dict | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def test_admin_status_reports_coverage_and_endpoints(server: str):
    payload = _get(server + "/admin/status")
    assert payload["chunks"] > 0
    assert payload["endpoints"]["mcp"].endswith("/mcp")
    assert payload["skill"]["target"]


def test_admin_config_is_editable_subset(server: str):
    payload = _get(server + "/admin/config")
    assert set(payload["config"]) <= {"roots", "formats", "scan", "index", "retrieval", "skill"}
    assert payload["config"]["roots"]


def test_admin_config_rejects_unknown_keys(server: str):
    payload = _get(server + "/admin/config")
    payload["config"]["bogus"] = 1
    with pytest.raises(urllib.error.HTTPError) as exc:
        _put(server + "/admin/config", {"config": payload["config"]})
    assert exc.value.code == 400


def test_admin_config_save_writes_and_reloads(server: str, config: Config):
    payload = _get(server + "/admin/config")
    editable = payload["config"]
    editable["roots"] = editable["roots"] + [
        {"name": "extra", "path": str(config.roots[0].path), "include": [], "exclude": []}
    ]
    saved = _put(server + "/admin/config", {"config": editable})
    assert "extra" in saved["roots"]
    reloaded = _get(server + "/admin/config")
    names = [root["name"] for root in reloaded["config"]["roots"]]
    assert "extra" in names
    assert config.source is not None and "extra" in config.source.read_text(encoding="utf-8")


def test_admin_build_dry_run_reports_without_writing(server: str):
    response = _post(server + "/admin/build", {"dry_run": True})
    assert response["build"]["dry_run"] is True
    assert response["build"]["added"] >= 0


def test_admin_skill_install(server: str, config: Config):
    response = _post(server + "/admin/skill", {"action": "install"})
    assert response["ok"] is True
    assert Path(response["path"]).is_file()


def test_admin_rejects_foreign_origin(server: str):
    request = urllib.request.Request(
        server + "/admin/config",
        data=json.dumps({"config": {}}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
        method="PUT",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=10)
    assert exc.value.code == 403


def test_removing_a_root_retires_its_documents(server: str, config_path: Path):
    before = _get(server + "/admin/status")
    assert len(before["roots"]) == 2
    payload = _get(server + "/admin/config")
    editable = payload["config"]
    editable["roots"] = [editable["roots"][0]]
    _put(server + "/admin/config", {"config": editable})
    after = _get(server + "/admin/status")
    assert len(after["roots"]) == 1


def test_rejected_save_leaves_the_file_untouched(server: str, config: Config):
    assert config.source is not None
    before = config.source.read_text(encoding="utf-8")
    payload = _get(server + "/admin/config")
    payload["config"]["scan"]["interval_seconds"] = 1
    with pytest.raises(urllib.error.HTTPError) as exc:
        _put(server + "/admin/config", {"config": payload["config"]})
    assert exc.value.code == 400
    assert config.source.read_text(encoding="utf-8") == before
