"""Configuration and management for a running index daemon.

The config file stays the source of truth: the console edits it through
``update_config`` and the daemon swaps in the validated result, so a hand edit
and a console edit cannot diverge. Saving rewrites the YAML, which drops
comments; the file keeps a header saying so.
"""

from __future__ import annotations

import copy
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from winnie_index import db, skill
from winnie_index.config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    config_from_mapping,
    load_config,
)
from winnie_index.scan import _drop_chunks, scan

EDITABLE = ("roots", "formats", "scan", "index", "retrieval", "skill")

HEADER = """\
# Winnie local index configuration.
# Edited by the admin console or by hand. Saving from the console rewrites this
# file and drops comments; the running daemon reloads it without a restart.
"""


class AdminError(RuntimeError):
    """A management request that cannot be applied."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Service:
    """The mutable part of the daemon: config, build lock and last result."""

    config: Config
    config_path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    last_build: dict[str, Any] | None = None

    @classmethod
    def from_config(cls, config: Config) -> "Service":
        path = config.source or DEFAULT_CONFIG_PATH.expanduser()
        return cls(config=config, config_path=path)

    # -- reading ---------------------------------------------------------

    def editable(self) -> dict[str, Any]:
        raw = copy.deepcopy(self.config.extra.get("raw", {}))
        config = {key: raw[key] for key in EDITABLE if key in raw}
        # Always return roots in the shape the console edits, even when the file
        # omitted optional keys.
        config["roots"] = [
            {
                "name": root.name,
                "path": str(root.path),
                "include": list(root.include),
                "exclude": list(root.exclude),
                "watch": root.watch,
            }
            for root in self.config.roots
        ]
        return config

    def status(self) -> dict[str, Any]:
        conn = db.connect(self.config.db_path)
        try:
            db.ensure_schema(conn)
            from winnie_index.search import status

            payload = status(conn, self.config)
        finally:
            conn.close()
        payload["config_path"] = str(self.config_path)
        payload["last_build"] = self.last_build
        payload["endpoints"] = {
            "mcp": f"http://{self.config.http_host}:{self.config.http_port}/mcp",
            "http": f"http://{self.config.http_host}:{self.config.http_port}",
        }
        payload["skill"] = {
            "target": str(self.config.skill_target),
            "installed": skill.check(self.config, self.config.skill_target)[0],
        }
        return payload

    # -- writing ---------------------------------------------------------

    def update_config(self, patch: dict[str, Any]) -> Config:
        if not isinstance(patch, dict):
            raise ConfigError("config must be an object")
        unknown = sorted(set(patch) - set(EDITABLE))
        if unknown:
            raise ConfigError(f"these keys are not editable here: {', '.join(unknown)}")

        layer = self._file_layer()
        merged = copy.deepcopy(layer)
        merged.update(copy.deepcopy(patch))

        # Validate before touching the file; a rejected edit must leave both the
        # file and the running daemon exactly as they were.
        candidate = config_from_mapping(merged, source=self.config_path)
        if candidate.data_dir != self.config.data_dir:
            raise ConfigError("data_dir cannot be changed from the console")
        if (candidate.http_host, candidate.http_port) != (
            self.config.http_host,
            self.config.http_port,
        ):
            raise ConfigError("serve.http cannot be changed from the console")

        previous = self.config_path.read_text(encoding="utf-8") if self.config_path.is_file() else None
        self._write(merged)
        reloaded = load_config(self.config_path, environ=dict(os.environ))
        if reloaded.data_dir != self.config.data_dir or (
            reloaded.http_host,
            reloaded.http_port,
        ) != (self.config.http_host, self.config.http_port):
            if previous is None:
                self.config_path.unlink(missing_ok=True)
            else:
                self.config_path.write_text(previous, encoding="utf-8")
            raise AdminError("configuration changed the data directory or listen address; reverted")

        removed = {root.name for root in self.config.roots} - {
            root.name for root in reloaded.roots
        }
        self.config = reloaded
        if removed:
            self._prune(removed)
        return reloaded

    def rebuild(self, *, dry_run: bool = False) -> dict[str, Any]:
        with self._lock:
            conn = db.connect(self.config.db_path)
            try:
                db.ensure_schema(conn)
                stats = scan(conn, self.config, dry_run=dry_run)
            finally:
                conn.close()
            self.last_build = {
                **stats.as_dict(),
                "at": _now(),
                "dry_run": dry_run,
                "errors": stats.errors[:10],
            }
            return self.last_build

    def install_skill(self) -> dict[str, Any]:
        path = skill.install(self.config, self.config.skill_target)
        return {"ok": True, "path": str(path)}

    def check_skill(self) -> dict[str, Any]:
        ok, path = skill.check(self.config, self.config.skill_target)
        return {"ok": ok, "path": str(path)}

    # -- internals -------------------------------------------------------

    def _file_layer(self) -> dict[str, Any]:
        if not self.config_path.is_file():
            return copy.deepcopy(self.config.extra.get("raw", {}))
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise AdminError(f"config is not a mapping: {self.config_path}")
        return raw

    def _write(self, layer: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.safe_dump(
            layer, sort_keys=False, allow_unicode=True, default_flow_style=False
        )
        temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temporary.write_text(HEADER + body, encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, self.config_path)

    def _prune(self, removed: set[str]) -> None:
        """Removing a root from the config also retires its documents."""
        conn = db.connect(self.config.db_path)
        try:
            db.ensure_schema(conn)
            for name in removed:
                row = conn.execute("SELECT id FROM roots WHERE name = ?", (name,)).fetchone()
                if row is None:
                    continue
                root_id = int(row[0])
                doc_ids = [
                    int(item[0])
                    for item in conn.execute("SELECT id FROM docs WHERE root_id = ?", (root_id,))
                ]
                for doc_id in doc_ids:
                    _drop_chunks(conn, doc_id)
                # Removing a root is explicit and destructive: the files stay on
                # disk, so re-adding the root rebuilds them, but the rows go so
                # the foreign key stays honest and no ghost root is listed.
                conn.execute("DELETE FROM docs WHERE root_id = ?", (root_id,))
                conn.execute("DELETE FROM roots WHERE id = ?", (root_id,))
            conn.commit()
        finally:
            conn.close()

