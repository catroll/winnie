"""Configuration.

Precedence is built-in defaults < YAML file < ``WINNIE_INDEX_*`` environment <
command-line flags. Unknown keys are rejected, because a typo that silently
does nothing is worse than a startup failure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("~/.config/winnie-index/config.yaml")
DEFAULT_DATA_DIR = Path("~/.local/share/winnie-index")
DEFAULT_SKILL_TARGET = Path("~/.agents/skills/local-knowledge")

DEFAULTS: dict[str, Any] = {
    "data_dir": str(DEFAULT_DATA_DIR),
    "roots": [],
    "formats": {"prose": ["txt", "md"]},
    "scan": {"interval_seconds": 300, "watch": False, "debounce_ms": 1500},
    "index": {
        "max_file_mb": 2,
        "max_chunks_per_doc": 2000,
        "chunk_tokens": 500,
        "overlap_tokens": 60,
    },
    "retrieval": {"limit": 8, "bm25_k": 50, "max_per_doc": 2},
    "serve": {"http": "127.0.0.1:8790", "mcp": "http", "token_env": "WINNIE_INDEX_TOKEN"},
    "skill": {"enabled": True, "target": str(DEFAULT_SKILL_TARGET)},
}

_ALLOWED: dict[str, set[str]] = {
    "": {"data_dir", "roots", "formats", "scan", "index", "retrieval", "serve", "skill"},
    "formats": {"prose"},
    "scan": {"interval_seconds", "watch", "debounce_ms"},
    "index": {"max_file_mb", "max_chunks_per_doc", "chunk_tokens", "overlap_tokens"},
    "retrieval": {"limit", "bm25_k", "max_per_doc"},
    "serve": {"http", "mcp", "token_env"},
    "skill": {"enabled", "target"},
}
_ROOT_ALLOWED = {"name", "path", "include", "exclude", "watch", "tier"}


class ConfigError(ValueError):
    """The configuration is missing, malformed, or contradictory."""


@dataclass(frozen=True)
class Root:
    name: str
    path: Path
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    watch: bool = False
    tier: str = "lexical"


@dataclass(frozen=True)
class Config:
    data_dir: Path
    roots: tuple[Root, ...]
    prose_ext: frozenset[str]
    scan_interval: int
    watch: bool
    max_file_bytes: int
    max_chunks: int
    chunk_tokens: int
    overlap_tokens: int
    limit: int
    bm25_k: int
    max_per_doc: int
    http_host: str
    http_port: int
    mcp: str
    token_env: str
    skill_enabled: bool
    skill_target: Path
    source: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "catalog.sqlite"


def _reject_unknown(section: str, data: Any) -> None:
    if not isinstance(data, dict):
        raise ConfigError(f"section {section or '<root>'} must be a mapping")
    allowed = _ALLOWED[section]
    unknown = sorted(set(data) - allowed)
    if unknown:
        where = section or "root"
        raise ConfigError(f"unknown key(s) in {where}: {', '.join(unknown)}")


def _merge(base: dict[str, Any], layer: dict[str, Any]) -> dict[str, Any]:
    merged = {key: (value.copy() if isinstance(value, dict) else value) for key, value in base.items()}
    for key, value in layer.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def _env_layer(environ: dict[str, str]) -> dict[str, Any]:
    layer: dict[str, Any] = {}
    data_dir = environ.get("WINNIE_INDEX_DATA_DIR")
    if data_dir:
        layer["data_dir"] = data_dir
    http = environ.get("WINNIE_INDEX_HTTP")
    if http:
        layer.setdefault("serve", {})["http"] = http
    mcp = environ.get("WINNIE_INDEX_MCP")
    if mcp:
        layer.setdefault("serve", {})["mcp"] = mcp
    return layer


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"config {path} must be a mapping")
    return raw


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> Config:
    environ = dict(os.environ) if environ is None else environ
    requested = path or environ.get("WINNIE_INDEX_CONFIG")
    default = DEFAULT_CONFIG_PATH.expanduser()
    source: Path | None = None
    if requested:
        source = Path(requested).expanduser()
        # The conventional path is optional however it arrives: a flag that
        # points at the default is still "no file asked for". Any other missing
        # path is a mistake worth stopping for.
        if not source.is_file() and source.resolve() != default.resolve():
            raise ConfigError(f"config file not found: {source}")
        file_layer = _load_yaml(source) if source.is_file() else {}
        source = source if source.is_file() else None
    else:
        file_layer = _load_yaml(default) if default.is_file() else {}
        source = default if default.is_file() else None

    validate_layer(file_layer)

    merged = _merge(DEFAULTS, file_layer)
    merged = _merge(merged, _env_layer(environ))
    if overrides:
        merged = _merge(merged, overrides)

    return _from_merged(merged, source=source)


def validate_layer(layer: dict[str, Any]) -> None:
    """Reject unknown keys at every level of a raw config mapping."""
    _reject_unknown("", layer)
    for section in ("formats", "scan", "index", "retrieval", "serve", "skill"):
        if section in layer:
            _reject_unknown(section, layer[section])
    for index, item in enumerate(layer.get("roots") or []):
        if not isinstance(item, dict):
            raise ConfigError(f"roots[{index}] must be a mapping")
        unknown = sorted(set(item) - _ROOT_ALLOWED)
        if unknown:
            raise ConfigError(f"unknown key(s) in roots[{index}]: {', '.join(unknown)}")


def config_from_mapping(
    layer: dict[str, Any], *, source: Path | None = None
) -> Config:
    """Validate a full config mapping, the same way a file would be validated."""
    validate_layer(layer)
    return _from_merged(_merge(DEFAULTS, layer), source=source)


def _from_merged(merged: dict[str, Any], *, source: Path | None) -> Config:
    roots: list[Root] = []
    seen: set[str] = set()
    for index, item in enumerate(merged.get("roots") or []):
        if not isinstance(item, dict) or "name" not in item or "path" not in item:
            raise ConfigError(f"roots[{index}] needs both name and path")
        name = str(item["name"]).strip()
        if not name:
            raise ConfigError(f"roots[{index}].name is empty")
        if name in seen:
            raise ConfigError(f"duplicate root name: {name}")
        seen.add(name)
        root_path = Path(str(item["path"])).expanduser()
        if not root_path.is_absolute():
            raise ConfigError(f"roots[{index}].path must be absolute: {root_path}")
        include = item.get("include") or []
        exclude = item.get("exclude") or []
        if not isinstance(include, list) or not isinstance(exclude, list):
            raise ConfigError(f"roots[{index}] include/exclude must be lists")
        roots.append(
            Root(
                name=name,
                path=root_path,
                include=tuple(str(value) for value in include),
                exclude=tuple(str(value) for value in exclude),
                watch=bool(item.get("watch", False)),
                tier=str(item.get("tier", "lexical")),
            )
        )

    prose = merged["formats"]["prose"]
    if not isinstance(prose, list) or not prose:
        raise ConfigError("formats.prose must be a non-empty list")
    prose_ext = frozenset(str(value).lower().lstrip(".") for value in prose)

    host, port = _parse_http(str(merged["serve"]["http"]))
    scan = merged["scan"]
    index = merged["index"]
    retrieval = merged["retrieval"]
    skill = merged["skill"]

    interval = int(scan["interval_seconds"])
    if not 5 <= interval <= 86400:
        raise ConfigError("scan.interval_seconds must be between 5 and 86400")
    if not 1 <= int(index["max_file_mb"]) <= 512:
        raise ConfigError("index.max_file_mb must be between 1 and 512")
    if not 1 <= int(retrieval["limit"]) <= 50:
        raise ConfigError("retrieval.limit must be between 1 and 50")
    if not 1 <= int(retrieval["max_per_doc"]) <= 10:
        raise ConfigError("retrieval.max_per_doc must be between 1 and 10")

    return Config(
        data_dir=Path(str(merged["data_dir"])).expanduser(),
        roots=tuple(roots),
        prose_ext=prose_ext,
        scan_interval=int(scan["interval_seconds"]),
        watch=bool(scan["watch"]),
        max_file_bytes=int(index["max_file_mb"]) * 1024 * 1024,
        max_chunks=int(index["max_chunks_per_doc"]),
        chunk_tokens=int(index["chunk_tokens"]),
        overlap_tokens=int(index["overlap_tokens"]),
        limit=int(retrieval["limit"]),
        bm25_k=int(retrieval["bm25_k"]),
        max_per_doc=int(retrieval["max_per_doc"]),
        http_host=host,
        http_port=port,
        mcp=str(merged["serve"]["mcp"]),
        token_env=str(merged["serve"]["token_env"]),
        skill_enabled=bool(skill["enabled"]),
        skill_target=Path(str(skill["target"])).expanduser(),
        source=source,
        extra={"raw": merged},
    )


def _parse_http(value: str) -> tuple[str, int]:
    host, _, port_text = value.rpartition(":")
    if not host or not port_text.isdigit():
        raise ConfigError(f"serve.http must be host:port, got {value!r}")
    port = int(port_text)
    if not 0 <= port < 65536:
        raise ConfigError(f"serve.http port out of range: {port}")
    return host, port


def config_template() -> str:
    """An annotated config with every key at its default."""
    return """\
# Winnie local index — every key below is optional and shows the default.
data_dir: ~/.local/share/winnie-index

# Roots to index. Nothing is indexed until at least one root exists.
# Each root needs a unique name and an absolute path.
roots: []
#  - name: notes
#    path: /home/you/Notes
#    include: []          # globs; empty means every matching file
#    exclude: ["**/private/**"]
#    watch: false         # inotify acceleration; the sweep is the backstop
#    tier: lexical        # reserved for the dense index

formats:
  prose: [txt, md]       # v1: text and Markdown only

scan:
  interval_seconds: 300  # one sweep every five minutes
  watch: false
  debounce_ms: 1500

index:
  max_file_mb: 2         # larger files are sealed, never read
  max_chunks_per_doc: 2000
  chunk_tokens: 500
  overlap_tokens: 60

retrieval:
  limit: 8               # results returned by default
  bm25_k: 50             # candidates pulled from FTS5 before filtering
  max_per_doc: 2         # at most this many chunks from one document

serve:
  http: 127.0.0.1:8790   # /search /doc /status /healthz and /mcp
  mcp: http              # http | stdio
  token_env: WINNIE_INDEX_TOKEN  # optional bearer token, empty means loopback trust

skill:
  enabled: true
  target: ~/.agents/skills/local-knowledge
"""
