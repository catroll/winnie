"""Winnie-issued gateway keys, stored in one YAML file.

The gateway used to accept a single shared ``proxy_api_key`` and forward the
caller's vendor key upstream. Both are gone: a caller now presents a Winnie key,
the gateway looks up the vendor credential that belongs to the route and injects
it. This is the only credential the caller ever holds.

The file, not the database, is the store. A gateway-only deployment has no
accounts, no meetings and therefore no schema, and a console-created key must
still work there. The file is mode ``0600`` and only a SHA-256 digest of each
secret is written; the secret itself is returned once, when it is created.

A key may be restricted to a set of route names. An empty ``routes`` list means
"every gateway route", which is the convenient default for a personal
deployment; a restriction is what makes a key that belongs to a phone or a
build agent harmless when it is copied somewhere else.

A key may also carry ``overrides``: a map from a published model name to
``vendor/model``, used when this caller must reach a different supplier than the
platform default. The name still has to be a published model — an override moves
where it is served, it does not invent a model.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from winnie_gateway.catalog import ModelTarget

SECRET_PREFIX = "wsk_"
FILE_VERSION = 1
MAX_LABEL = 64
MAX_ROUTES = 64
MAX_OVERRIDES = 64


class GatewayKeyError(RuntimeError):
    """A key operation the caller asked for is not possible."""


class UnknownGatewayKey(GatewayKeyError):
    """No key has the requested id."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class GatewayKey(BaseModel):
    """One stored key: the caller-facing record, never a usable credential.

    ``owner_id`` is the account that created the key. A key without an owner
    predates per-account ownership and can only be managed by an administrator;
    ``owner_name`` is a snapshot for display, because the key file must stay
    readable without a database.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    digest: str
    created_at: str
    revoked_at: str | None = None
    routes: list[str] = Field(default_factory=list)
    # A published model name -> "vendor/model". Applied after the platform
    # catalog resolves the name, so an override can only move a name that
    # already exists (see ``ModelCatalog.target_for``).
    overrides: dict[str, str] = Field(default_factory=dict)
    owner_id: str = ""
    owner_name: str = ""

    @field_validator("overrides")
    @classmethod
    def _overrides(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for name, target in value.items():
            key = str(name).strip()
            if not key:
                raise ValueError("an override needs a model name")
            if ModelTarget.parse(str(target)) is None:
                raise ValueError(
                    f"override for {key!r} must be written 'vendor/model', "
                    f"got {target!r}"
                )
            cleaned[key] = str(target).strip()
        if len(cleaned) > MAX_OVERRIDES:
            raise ValueError(
                f"a gateway key may override at most {MAX_OVERRIDES} models"
            )
        return cleaned

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def allows(self, route: str) -> bool:
        """Whether this key may call ``route``; revoked keys never may."""
        if self.revoked:
            return False
        return not self.routes or route in self.routes

    def view(self) -> dict[str, Any]:
        """What the console may see. The digest is not part of it."""
        return {
            "id": self.id,
            "label": self.label,
            "created_at": self.created_at,
            "revoked_at": self.revoked_at,
            "routes": list(self.routes),
            "overrides": dict(self.overrides),
            "owner_id": self.owner_id,
            "owner_name": self.owner_name,
        }


class _KeyFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = FILE_VERSION
    keys: list[GatewayKey] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _known_version(cls, value: int) -> int:
        if value != FILE_VERSION:
            raise ValueError(
                f"gateway key file version {value} is not supported "
                f"(this build reads version {FILE_VERSION})"
            )
        return value


def _validate_label(label: str) -> str:
    cleaned = label.strip()
    if not cleaned:
        raise GatewayKeyError("a gateway key needs a label")
    if len(cleaned) > MAX_LABEL:
        raise GatewayKeyError(f"a gateway key label must be at most {MAX_LABEL} characters")
    return cleaned


def _normalise_routes(routes: list[str] | tuple[str, ...] | None) -> list[str]:
    seen: list[str] = []
    for name in routes or []:
        cleaned = name.strip()
        if not cleaned:
            raise GatewayKeyError("route names must not be empty")
        if cleaned not in seen:
            seen.append(cleaned)
    if len(seen) > MAX_ROUTES:
        raise GatewayKeyError(f"a gateway key may name at most {MAX_ROUTES} routes")
    return seen


def _normalise_overrides(
    overrides: dict[str, str] | None,
) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for name, target in (overrides or {}).items():
        key = str(name).strip()
        if not key:
            raise GatewayKeyError("an override needs a model name")
        parsed = ModelTarget.parse(str(target))
        if parsed is None:
            raise GatewayKeyError(
                f"override for {key!r} must be written 'vendor/model', "
                f"got {target!r}"
            )
        cleaned[key] = parsed.key()
    if len(cleaned) > MAX_OVERRIDES:
        raise GatewayKeyError(
            f"a gateway key may override at most {MAX_OVERRIDES} models"
        )
    return cleaned


class GatewayKeyStore:
    """Load, issue and revoke gateway keys.

    The file is re-read when its mtime changes, so a console write in one
    process is visible to a worker that shares the file without a restart. All
    mutations hold a lock and replace the file atomically.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()
        self._keys: list[GatewayKey] = []
        self._mtime: float | None = None
        self._load()

    # -- reading ---------------------------------------------------------

    def list(self) -> list[GatewayKey]:
        with self._lock:
            self._maybe_reload()
            return sorted(self._keys, key=lambda key: (key.created_at, key.id))

    def find(self, key_id: str) -> GatewayKey:
        with self._lock:
            self._maybe_reload()
            for key in self._keys:
                if key.id == key_id:
                    return key
        raise UnknownGatewayKey(f"gateway key {key_id!r} does not exist")

    def verify(self, secret: str) -> GatewayKey | None:
        """The active key a presented secret belongs to, if any."""
        candidate = secret.strip()
        if not candidate.startswith(SECRET_PREFIX):
            return None
        wanted = digest_secret(candidate)
        with self._lock:
            self._maybe_reload()
            for key in self._keys:
                if key.revoked:
                    continue
                if hmac.compare_digest(key.digest, wanted):
                    return key
        return None

    # -- writing ---------------------------------------------------------

    def create(
        self,
        *,
        label: str,
        routes: list[str] | tuple[str, ...] | None = None,
        overrides: dict[str, str] | None = None,
        owner_id: str = "",
        owner_name: str = "",
    ) -> tuple[GatewayKey, str]:
        """Issue one key and return it with the secret, which is stored nowhere."""
        cleaned = _validate_label(label)
        allowed = _normalise_routes(routes)
        override = _normalise_overrides(overrides)
        secret = f"{SECRET_PREFIX}{secrets.token_urlsafe(32)}"
        key = GatewayKey(
            id=secrets.token_hex(4),
            label=cleaned,
            digest=digest_secret(secret),
            created_at=utcnow(),
            routes=allowed,
            overrides=override,
            owner_id=owner_id.strip(),
            owner_name=owner_name.strip(),
        )
        with self._lock:
            self._maybe_reload()
            self._keys.append(key)
            self._write()
        return key, secret

    def revoke(self, key_id: str) -> GatewayKey:
        """Mark a key unusable. The record stays so old usage stays attributable."""
        with self._lock:
            self._maybe_reload()
            for index, key in enumerate(self._keys):
                if key.id != key_id:
                    continue
                if key.revoked:
                    return key
                revoked = key.model_copy(update={"revoked_at": utcnow()})
                self._keys[index] = revoked
                self._write()
                return revoked
        raise UnknownGatewayKey(f"gateway key {key_id!r} does not exist")

    # -- file handling ---------------------------------------------------

    def _maybe_reload(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        if mtime != self._mtime:
            self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            self._keys = []
            self._mtime = None
            return
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise GatewayKeyError(f"cannot read gateway keys at {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise GatewayKeyError(f"gateway keys must be a mapping: {self.path}")
        try:
            parsed = _KeyFile.model_validate(raw)
        except ValidationError as exc:
            raise GatewayKeyError(f"invalid gateway keys at {self.path}: {exc}") from exc
        ids = [key.id for key in parsed.keys]
        duplicates = {key_id for key_id in ids if ids.count(key_id) > 1}
        if duplicates:
            raise GatewayKeyError(
                f"duplicate gateway key ids at {self.path}: {', '.join(sorted(duplicates))}"
            )
        self._keys = list(parsed.keys)
        self._mtime = self.path.stat().st_mtime

    def _write(self) -> None:
        payload = _KeyFile(keys=self._keys).model_dump(mode="json")
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".gateway-keys-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        self._mtime = self.path.stat().st_mtime
