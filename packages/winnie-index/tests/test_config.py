from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from winnie_index.config import ConfigError, load_config


def _write(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_defaults_apply_from_an_empty_file(tmp_path: Path):
    path = _write(tmp_path / "c.yaml", {})
    config = load_config(path, environ={})
    assert config.prose_ext == {"txt", "md"}
    assert config.http_port == 8790
    assert config.data_dir.name == "winnie-index"


def test_explicit_missing_file_is_an_error(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml", environ={})


def test_unknown_key_is_rejected(tmp_path: Path):
    path = _write(tmp_path / "c.yaml", {"roots": [], "typo": 1})
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path, environ={})


def test_unknown_root_key_is_rejected(tmp_path: Path):
    path = _write(tmp_path / "c.yaml", {"roots": [{"name": "a", "path": "/tmp", "bogus": 1}]})
    with pytest.raises(ConfigError, match="roots\\[0\\]"):
        load_config(path, environ={})


def test_duplicate_root_names_are_rejected(tmp_path: Path):
    path = _write(
        tmp_path / "c.yaml",
        {"roots": [{"name": "a", "path": "/tmp"}, {"name": "a", "path": "/var"}]},
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(path, environ={})


def test_relative_root_path_is_rejected(tmp_path: Path):
    path = _write(tmp_path / "c.yaml", {"roots": [{"name": "a", "path": "relative/dir"}]})
    with pytest.raises(ConfigError, match="absolute"):
        load_config(path, environ={})


def test_environment_overrides_yaml(tmp_path: Path):
    path = _write(tmp_path / "c.yaml", {"data_dir": str(tmp_path / "from-yaml")})
    config = load_config(path, environ={"WINNIE_INDEX_DATA_DIR": str(tmp_path / "from-env")})
    assert config.data_dir == tmp_path / "from-env"


def test_flags_override_environment(tmp_path: Path):
    path = _write(tmp_path / "c.yaml", {})
    config = load_config(
        path,
        environ={"WINNIE_INDEX_DATA_DIR": str(tmp_path / "from-env")},
        overrides={"data_dir": str(tmp_path / "from-flag")},
    )
    assert config.data_dir == tmp_path / "from-flag"


def test_http_must_be_host_and_port(tmp_path: Path):
    path = _write(tmp_path / "c.yaml", {"serve": {"http": "nonsense"}})
    with pytest.raises(ConfigError, match="host:port"):
        load_config(path, environ={})


def test_scan_interval_has_bounds(tmp_path: Path):
    path = _write(tmp_path / "c.yaml", {"scan": {"interval_seconds": 86401}})
    with pytest.raises(ConfigError, match="interval_seconds"):
        load_config(path, environ={})


def test_retrieval_limit_has_bounds(tmp_path: Path):
    path = _write(tmp_path / "c.yaml", {"retrieval": {"limit": 500}})
    with pytest.raises(ConfigError, match="retrieval.limit"):
        load_config(path, environ={})


def test_missing_default_path_is_not_an_error():
    from winnie_index.config import DEFAULT_CONFIG_PATH

    path = DEFAULT_CONFIG_PATH.expanduser()
    if path.exists():
        pytest.skip("a real user config exists on this machine")
    config = load_config(path, environ={})
    assert config.roots == ()
