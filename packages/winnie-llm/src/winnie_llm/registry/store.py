"""Read, validate and persist the supplier registry, and build clients.

One small YAML file keeps the state. It is read on demand (a changed mtime is
picked up automatically, so an arq worker sees a console change without a
restart) and written atomically. Each supplier holds its own API key next to its
model catalog, so an internal model call reads the credential from the same
record that names its endpoint. The console mirrors every saved key into
``packages/winnie-llm/.env`` as a backup; that file is not a source.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import ValidationError

from winnie_llm.asr.base import ASRProvider
from winnie_llm.asr.config import VolcengineASRConfig
from winnie_llm.asr.fake import FakeASRProvider, FakeRealtimeASR
from winnie_llm.asr.realtime import RealtimeASRProvider
from winnie_llm.asr.volcengine import VolcengineASRProvider
from winnie_llm.asr.volcengine_stream import VolcengineRealtimeASR
from winnie_llm.chat import ChatClient
from winnie_llm.keys import ApiKeys
from winnie_llm.registry.errors import AIConflict, AINotFound, AIProviderError
from winnie_llm.registry.schema import (
    AIState,
    BindingDraft,
    BindingRecord,
    FEATURE_BY_KEY,
    FEATURES,
    IntegrationDraft,
    IntegrationRecord,
    KINDS,
    KIND_BY_KEY,
    ModelRecord,
    ResolvedProvider,
    RouteEntry,
    VendorCheck,
    VendorDraft,
    VendorRecord,
    VirtualModelDraft,
    VirtualModelRecord,
    infer_vendor,
    utcnow,
    vendor_label_for,
)
from winnie_llm.registry.seed import (
    SeedInput,
    migrate_state,
    seed_state,
    virtualise_bindings,
)
from winnie_llm.runtime import ModelRuntimeConfig

logger = logging.getLogger(__name__)

_HEADER = (
    "# Winnie AI suppliers and feature bindings.\n"
    "# Written by the admin console at /api/v1/ai; use the console rather than\n"
    "# editing this file. Each supplier's api_key is stored here with its models,\n"
    "# and the console mirrors it into packages/winnie-llm/.env as a backup.\n"
)


def _key_hint(value: str) -> str:
    """The last four characters of a key, for telling two keys apart."""
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 8:
        return "…"
    return f"…{cleaned[-4:]}"



class ProviderRegistry:
    """Own the file, expose CRUD, and build clients for a feature."""

    def __init__(
        self,
        path: Path | str,
        *,
        keys: ApiKeys | None = None,
        seed: SeedInput | None = None,
    ) -> None:
        self.path = Path(path)
        self.keys = keys if keys is not None else ApiKeys.load()
        self.seed = seed or SeedInput()
        self._lock = threading.RLock()
        self._state: AIState | None = None
        self._mtime_ns: int | None = None

    # -- lifecycle -------------------------------------------------------

    def load(self) -> None:
        """Load the file, or seed it from the host's seed input on first start."""
        with self._lock:
            if not self.path.is_file():
                state = seed_state(self.seed)
                # A first start after the upgrade (or a fresh registry on a host
                # whose env backup already holds keys) imports them once, so
                # deleting data/ai.yaml does not lose the credentials.
                self._import_backup_keys(state)
                self._write(state)
                return
            self._read()

    @staticmethod
    def _strip_legacy_keys(raw: dict[str, Any]) -> dict[str, str]:
        """Remove the v5 ``api_key_env`` field and return it for migration.

        Version 6 derives the variable from the supplier id. An older file
        still carries the field, so it must be dropped before validation; its
        value is the only way to recognise a v1 combined internal route.
        """
        legacy: dict[str, str] = {}
        integrations = raw.get("integrations")
        if not isinstance(integrations, list):
            return legacy
        for item in integrations:
            if not isinstance(item, dict):
                continue
            value = item.pop("api_key_env", "")
            if isinstance(value, str) and value.strip():
                name = item.get("name")
                if isinstance(name, str):
                    legacy[name] = value.strip()
        return legacy

    def _read(self) -> None:
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise AIProviderError(
                f"cannot read AI settings at {self.path}: {exc}"
            ) from exc
        if raw is None:
            raise AIProviderError(f"AI settings file is empty: {self.path}")
        if not isinstance(raw, dict):
            raise AIProviderError(f"invalid AI settings at {self.path}: not a mapping")
        legacy_keys = self._strip_legacy_keys(raw)
        try:
            state = AIState.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - pydantic's message is the detail
            raise AIProviderError(
                f"invalid AI settings at {self.path}: {exc}"
            ) from exc
        changed = migrate_state(state, legacy_keys)
        if state.version < 7:
            # The first read after the upgrade: keys used to live only in the
            # env backup, so move them onto their supplier once. This is the
            # only time the backup is read as a source.
            self._import_backup_keys(state)
            state.version = 7
            changed = True
        # Virtual models were added in version 8. The migration runs after the
        # key import so a version 5 file still picks its keys up from the
        # backup on the same read.
        if virtualise_bindings(state):
            changed = True
        if changed:
            try:
                state = AIState.model_validate(state.model_dump(mode="json"))
            except ValidationError as exc:
                raise AIProviderError(
                    f"migrated AI settings are invalid at {self.path}: {exc}"
                ) from exc
            self._write(state)
            return
        self._state = state
        self._mtime_ns = self.path.stat().st_mtime_ns

    def _import_backup_keys(self, state: AIState) -> None:
        """Copy each supplier key from the env backup onto its record."""
        for vendor in state.vendors:
            if vendor.api_key:
                continue
            vendor.api_key = self.keys.get(vendor.id)

    def _refresh(self) -> None:
        """Pick up an external edit without holding stale state forever."""
        if self._state is None:
            self.load()
            return
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            self.load()
            return
        if mtime != self._mtime_ns:
            self._read()

    def state(self) -> AIState:
        with self._lock:
            self._refresh()
            assert self._state is not None
            return self._state

    def _write(self, state: AIState) -> None:
        payload = state.model_dump(mode="json")
        text = _HEADER + yaml.safe_dump(
            payload,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(text)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self._state = state
        self._mtime_ns = self.path.stat().st_mtime_ns

    def _commit(self, state: AIState) -> None:
        try:
            validated = AIState.model_validate(state.model_dump(mode="json"))
        except ValidationError as exc:
            raise AIProviderError(
                f"the change would make the AI registry invalid: {exc}"
            ) from exc
        self._write(validated)

    # -- reads -----------------------------------------------------------

    def _internal_integration(
        self, state: AIState, vendor_id: str, kinds: tuple[str, ...]
    ) -> IntegrationRecord | None:
        """The endpoint the host uses to call a supplier, never a gateway route."""
        for integration in state.integrations:
            if integration.vendor != vendor_id or not integration.enabled:
                continue
            if integration.gateway:
                continue
            if integration.kind not in kinds:
                continue
            return integration
        return None

    @staticmethod
    def _bound_targets(
        state: AIState, vendor_id: str
    ) -> list[tuple[str, str]]:
        """``(feature, model)`` for every binding that resolves to a supplier.

        A virtual binding follows its alias, so "which features use this
        supplier's model" has a different answer before and after the alias
        moves. Asking here keeps that answer in one place.
        """
        targets: list[tuple[str, str]] = []
        for feature, binding in state.bindings.items():
            if binding.virtual:
                virtual = state.virtual_model(binding.virtual)
                if virtual is None or virtual.vendor != vendor_id:
                    continue
                targets.append((feature, virtual.model))
            elif binding.vendor == vendor_id and binding.model:
                targets.append((feature, binding.model))
        return targets

    def resolve(self, feature: str) -> ResolvedProvider:
        spec = FEATURE_BY_KEY.get(feature)
        if spec is None:
            raise AINotFound(f"unknown AI feature: {feature}")
        state = self.state()
        binding = state.bindings.get(feature)
        virtual_id = binding.virtual if binding is not None else ""
        if virtual_id:
            virtual = state.virtual_model(virtual_id)
            if virtual is None:
                raise AIProviderError(
                    f"feature {feature} references missing virtual model "
                    f"{virtual_id!r}"
                )
            vendor_id = virtual.vendor
            model = virtual.model
        else:
            vendor_id = binding.vendor if binding is not None else ""
            model = binding.model if binding is not None else ""
        legacy_integration = (
            state.integration(binding.integration_id)
            if binding is not None and binding.integration_id
            else None
        )
        if legacy_integration is not None:
            # A v3 binding that has not been migrated yet.
            vendor_id = legacy_integration.vendor
            model = model or legacy_integration.default_model
        if not vendor_id:
            raise AIProviderError(
                f"feature {feature} has no model; choose one in "
                "Admin -> AI suppliers"
            )
        vendor = state.vendor(vendor_id)
        if vendor is None:
            raise AIProviderError(
                f"feature {feature} references missing supplier {vendor_id!r}"
            )
        integration = self._internal_integration(state, vendor.id, spec.kinds)
        if integration is None:
            raise AIProviderError(
                f"supplier {vendor.label!r} has no enabled internal endpoint for "
                f"{feature}; add its API endpoint and key in Admin -> AI suppliers"
            )
        if integration.kind == "fake":
            model = ""
        else:
            model = model or integration.default_model
            if spec.requires_model and not model:
                raise AIProviderError(
                    f"feature {feature} needs a model from {vendor.label!r}"
                )
            if model and not any(item.id == model for item in vendor.models):
                raise AIProviderError(
                    f"model {model!r} is not in the {vendor.label} catalog; "
                    "sync the supplier models first"
                )
        return ResolvedProvider(
            feature=feature,
            source="admin",
            integration_id=integration.id,
            integration_name=integration.name,
            vendor=vendor.id,
            vendor_label=vendor.label,
            kind=integration.kind,
            base_url=integration.base_url,
            key_env=self.keys.env_name(vendor.id),
            model=model,
            options=dict(integration.options),
            virtual=virtual_id,
        )

    def route_entries(self) -> list[RouteEntry]:
        """Gateway routes the host should expose for the current state."""
        entries: list[RouteEntry] = []
        for integration in self.state().integrations:
            if not integration.enabled:
                continue
            if integration.kind not in {"openai", "anthropic"}:
                continue
            if not integration.gateway:
                continue
            entries.append(
                RouteEntry(
                    name=integration.name,
                    protocol=integration.kind,
                    base_url=integration.base_url,
                    vendor=integration.vendor,
                )
            )
        return entries

    def runtime(self) -> ModelRuntimeConfig:
        return self.state().runtime

    def set_runtime(self, runtime: ModelRuntimeConfig) -> ModelRuntimeConfig:
        with self._lock:
            state = self.state().model_copy(deep=True)
            state.runtime = runtime
            self._commit(state)
            return runtime

    # -- clients ---------------------------------------------------------

    def vendor_key(self, vendor_id: str) -> str:
        """The supplier's stored credential, or ``""`` when it has none."""
        vendor = self.state().vendor(vendor_id)
        return vendor.api_key if vendor is not None else ""

    def key_present(self, vendor_id: str) -> bool:
        return bool(self.vendor_key(vendor_id))

    def key_env(self, vendor_id: str) -> str:
        """The env variable the console mirrors this supplier's key into."""
        return self.keys.env_name(vendor_id)

    def _require_key(self, vendor_id: str) -> str:
        value = self.vendor_key(vendor_id)
        if not value:
            raise AIProviderError(
                f"supplier {vendor_id!r} has no API key; set it in "
                "Admin -> AI suppliers"
            )
        return value

    def _mirror_key(self, vendor_id: str, value: str) -> None:
        """Keep the env backup equal to the stored key.

        The registry is authoritative, so a failed mirror is logged rather than
        failing the save: losing the backup is recoverable, losing the key the
        owner just pasted is not.
        """
        try:
            self.keys.set(vendor_id, value)
        except (OSError, ValueError) as exc:
            logger.warning(
                "cannot mirror the %s key into %s: %s",
                vendor_id,
                self.keys.path,
                exc,
            )

    def set_api_key(self, vendor_id: str, api_key: str) -> VendorRecord:
        """Store a supplier key with its record and refresh the env backup."""
        value = api_key.strip()
        with self._lock:
            state = self.state().model_copy(deep=True)
            vendor = state.vendor(vendor_id)
            if vendor is None:
                raise AINotFound(f"supplier not found: {vendor_id}")
            vendor.api_key = value
            vendor.updated_at = utcnow()
            self._commit(state)
        self._mirror_key(vendor_id, value)
        return vendor

    def chat_for(
        self,
        feature: str,
        *,
        http: httpx.AsyncClient | None = None,
        usage: Any = None,
    ) -> ChatClient:
        """One OpenAI-compatible client bound to a feature's supplier model."""
        provider = self.resolve(feature)
        if provider.kind != "openai":
            raise AIProviderError(
                f"{feature} cannot use a {provider.kind} supplier; choose an "
                "OpenAI-compatible one in Admin -> AI suppliers"
            )
        runtime = self.runtime()
        return ChatClient(
            base_url=_openai_base(provider.base_url),
            api_key=self._require_key(provider.vendor),
            model=provider.model,
            timeout_seconds=runtime.timeout_seconds,
            max_output_tokens=runtime.max_output_tokens,
            temperature=runtime.temperature,
            max_retries=runtime.max_retries,
            retry_backoff_seconds=runtime.retry_backoff_seconds,
            http=http,
            usage=usage,
            capability=provider.feature,
            provider=provider.integration_name,
        )

    def asr_for(
        self,
        feature: str,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> ASRProvider:
        """A recording-file speech client bound to a feature's supplier model."""
        provider = self.resolve(feature)
        if provider.kind == "fake":
            return FakeASRProvider()
        if provider.kind != "volcengine_asr":
            raise AIProviderError(
                f"{feature} cannot use a {provider.kind} supplier; choose "
                "Volcengine ASR in Admin -> AI suppliers"
            )
        config = _volcengine_config(provider)
        return VolcengineASRProvider(
            api_key=self._require_key(provider.vendor),
            base_url=config.base_url,
            resource_id=config.resource_id,
            model_name=config.model_name,
            max_inline_bytes=config.max_inline_mb * 1024 * 1024,
            poll_interval_seconds=config.poll_interval_seconds,
            poll_timeout_seconds=config.poll_timeout_seconds,
            http=http,
        )

    def realtime_asr_for(self, feature: str) -> RealtimeASRProvider:
        """A streaming speech client bound to a feature's supplier model."""
        provider = self.resolve(feature)
        if provider.kind == "fake":
            return FakeRealtimeASR()
        if provider.kind != "volcengine_asr":
            raise AIProviderError(
                f"{feature} cannot use a {provider.kind} supplier; choose "
                "Volcengine ASR in Admin -> AI suppliers"
            )
        config = _volcengine_config(provider)
        return VolcengineRealtimeASR(
            api_key=self._require_key(provider.vendor),
            endpoint=config.stream_endpoint,
            resource_id=config.stream_resource_id,
            model_name=config.model_name,
        )

    async def sync_models(
        self, vendor_id: str, http: httpx.AsyncClient | None = None
    ) -> VendorRecord:
        """Fetch the supplier's supported models from its OpenAI /models API."""
        state = self.state()
        vendor = state.vendor(vendor_id)
        if vendor is None:
            raise AINotFound(f"supplier not found: {vendor_id}")
        integration = self._internal_integration(
            state, vendor_id, ("openai",)
        )
        if integration is None:
            raise AIProviderError(
                f"supplier {vendor.label!r} has no OpenAI-compatible API endpoint"
            )
        if not integration.base_url:
            raise AIProviderError(
                f"supplier {vendor.label!r} needs a base URL for model sync"
            )
        api_key = self._require_key(vendor.id)
        base = _openai_base(integration.base_url)
        url = f"{base}/models"
        own_client = http is None
        client = http or httpx.AsyncClient(timeout=15)
        try:
            try:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=15,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise AIProviderError(
                    f"model sync for {vendor.label!r} failed at {url}: {exc}"
                ) from exc
            try:
                payload = response.json()
            except ValueError as exc:
                raise AIProviderError(
                    f"model sync for {vendor.label!r} returned non-JSON"
                ) from exc
        finally:
            if own_client:
                await client.aclose()

        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise AIProviderError(
                f"model sync for {vendor.label!r} returned no data list"
            )
        fetched: list[ModelRecord] = []
        existing = {model.id: model for model in vendor.models}
        for item in items:
            if isinstance(item, str):
                model_id = item.strip()
                owned_by = ""
                created = None
            elif isinstance(item, dict):
                model_id = str(item.get("id") or "").strip()
                owned_by = str(item.get("owned_by") or "").strip()
                raw_created = item.get("created")
                created = raw_created if isinstance(raw_created, int) else None
            else:
                continue
            if not model_id:
                continue
            old = existing.get(model_id)
            fetched.append(
                ModelRecord(
                    id=model_id,
                    label=(old.label if old is not None and old.label != old.id else model_id),
                    owned_by=owned_by or (old.owned_by if old is not None else ""),
                    created=created if created is not None else (old.created if old is not None else None),
                    context_window=old.context_window if old is not None else None,
                    max_output_tokens=old.max_output_tokens if old is not None else None,
                    notes=old.notes if old is not None else "",
                )
            )
        if not fetched:
            raise AIProviderError(
                f"model sync for {vendor.label!r} returned an empty list"
            )

        fetched_ids = {model.id for model in fetched}
        used = {model for _, model in self._bound_targets(state, vendor_id)} | {
            virtual.model
            for virtual in state.virtual_models
            if virtual.vendor == vendor_id
        } | {
            integration.default_model
            for integration in state.integrations
            if integration.vendor == vendor_id and integration.default_model
        }
        kept = [
            model
            for model in vendor.models
            if model.id not in fetched_ids and model.id in used
        ]

        with self._lock:
            current = self.state().model_copy(deep=True)
            target = current.vendor(vendor_id)
            if target is None:
                raise AINotFound(f"supplier not found: {vendor_id}")
            target.models = fetched + kept
            target.updated_at = utcnow()
            self._commit(current)
            return target

    async def check_vendor(
        self, vendor_id: str, http: httpx.AsyncClient | None = None
    ) -> VendorCheck:
        """Probe the supplier's own API with the key stored on its record.

        The probe is ``GET /models``: it spends no tokens, authenticates with
        exactly the credential an internal call or the gateway would send, and
        answers both questions a saved key raises — whether the supplier
        accepts it, and whether the models the features are bound to still
        exist. It does not prove that a completion succeeds; the console says
        that next to the result rather than pretending otherwise.
        """
        state = self.state()
        vendor = state.vendor(vendor_id)
        if vendor is None:
            raise AINotFound(f"supplier not found: {vendor_id}")
        api_key = self.vendor_key(vendor.id)
        if not api_key:
            raise AIConflict(
                f"supplier {vendor.label!r} has no API key; save one before "
                "checking it"
            )
        target = _probe_target(state, vendor.id)
        if target is None:
            return VendorCheck(
                state="unsupported",
                detail=(
                    f"supplier {vendor.label!r} has no enabled OpenAI or "
                    "Anthropic API to probe"
                ),
            )
        url, protocol = target
        own_client = http is None
        client = http or httpx.AsyncClient(timeout=10)
        try:
            try:
                response = await client.get(
                    url, headers=_probe_headers(protocol, api_key), timeout=10
                )
            except httpx.HTTPError as exc:
                return VendorCheck(
                    state="error", url=url, detail=f"{type(exc).__name__}: {exc}"
                )
        finally:
            if own_client:
                await client.aclose()

        if response.status_code in (401, 403):
            return VendorCheck(
                state="unauthorized",
                status=response.status_code,
                url=url,
                detail=_excerpt(response),
            )
        if response.status_code >= 400:
            return VendorCheck(
                state="error",
                status=response.status_code,
                url=url,
                detail=_excerpt(response),
            )
        try:
            payload = response.json()
        except ValueError:
            return VendorCheck(
                state="error",
                status=response.status_code,
                url=url,
                detail="the supplier returned a non-JSON model list",
            )
        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return VendorCheck(
                state="error",
                status=response.status_code,
                url=url,
                detail="the supplier returned no model list",
            )
        catalog = {
            str(item.get("id") if isinstance(item, dict) else item).strip()
            for item in items
        }
        catalog.discard("")
        # A key that works can still point at a model the supplier retired;
        # that is the other way a configured feature fails at call time, and
        # the model list already in hand answers it for free.
        missing = tuple(
            f"{feature} · {model}"
            for feature, model in self._bound_targets(state, vendor.id)
            if model not in catalog
        )
        return VendorCheck(
            state="ok",
            status=response.status_code,
            url=url,
            models=len(catalog),
            missing_models=missing,
        )

    def view(self) -> dict[str, Any]:
        state = self.state()
        return {
            "path": str(self.path),
            # The env backup the console mirrors every supplier key into. The
            # values themselves are stored in this registry.
            "env_path": str(self.keys.path),
            "runtime": state.runtime.model_dump(mode="json"),
            "vendors": [
                self._vendor_view(vendor, state) for vendor in state.vendors
            ],
            "integrations": [
                self._integration_view(integration, state)
                for integration in state.integrations
            ],
            "virtual_models": [
                self._virtual_view(virtual, state)
                for virtual in state.virtual_models
            ],
            "features": [self._feature_view(feature.key, state) for feature in FEATURES],
            "kinds": [
                {
                    "kind": kind.kind,
                    "label": kind.label,
                    "description": kind.description,
                    "gateway": kind.gateway,
                    "requires_base_url": kind.requires_base_url,
                    "features": [
                        feature.key
                        for feature in FEATURES
                        if kind.kind in feature.kinds
                    ],
                }
                for kind in KINDS
                if kind.kind != "fake"
            ],
            "updated_at": max(
                [vendor.updated_at for vendor in state.vendors]
                + [integration.updated_at for integration in state.integrations]
                + [virtual.updated_at for virtual in state.virtual_models]
                + [binding.updated_at for binding in state.bindings.values()],
                default="",
            ),
        }

    def _vendor_view(self, vendor: VendorRecord, state: AIState) -> dict[str, Any]:
        endpoints = [
            integration
            for integration in state.integrations
            if integration.vendor == vendor.id
        ]
        internal = next(
            (
                integration
                for integration in endpoints
                if not integration.gateway
                and integration.kind in {"openai", "volcengine_asr", "fake"}
            ),
            None,
        )
        # The key itself never leaves the server; the console only needs to
        # know whether one is set and which one it is.
        stored = vendor.model_dump(mode="json", exclude={"api_key"})
        return {
            **stored,
            "integration_count": len(endpoints),
            "gateway_count": sum(1 for integration in endpoints if integration.gateway),
            "internal_integration_id": internal.id if internal is not None else "",
            "kind": internal.kind if internal is not None else "",
            "base_url": internal.base_url if internal is not None else "",
            # The variable the backup copy is written under, so an operator can
            # still find the key in packages/winnie-llm/.env.
            "api_key_env": self.keys.env_name(vendor.id),
            "api_key_present": bool(vendor.api_key),
            "api_key_hint": _key_hint(vendor.api_key),
            "api_key_backup_path": str(self.keys.path),
            "enabled": internal.enabled if internal is not None else False,
        }

    def _integration_view(
        self, integration: IntegrationRecord, state: AIState
    ) -> dict[str, Any]:
        used_by = [
            feature
            for feature, binding in state.bindings.items()
            if binding.integration_id == integration.id
        ]
        kind = KIND_BY_KEY[integration.kind]
        paths: list[str] = []
        if integration.enabled and integration.gateway and kind.gateway:
            if integration.kind == "openai":
                paths = [
                    f"/{integration.name}/v1/chat/completions",
                    f"/{integration.name}/v1/models",
                ]
            else:
                paths = [
                    f"/{integration.name}/v1/messages",
                    f"/{integration.name}/v1/models",
                ]
        vendor = state.vendor(integration.vendor)
        return {
            **integration.model_dump(mode="json"),
            "kind_label": kind.label,
            "vendor_label": vendor.label if vendor is not None else integration.vendor,
            "api_key_env": self.keys.env_name(integration.vendor),
            "api_key_present": bool(vendor.api_key) if vendor is not None else False,
            "used_by": sorted(used_by),
            "gateway_paths": paths,
        }

    def _virtual_view(
        self, virtual: VirtualModelRecord, state: AIState
    ) -> dict[str, Any]:
        vendor = state.vendor(virtual.vendor)
        model = next(
            (
                item
                for item in (vendor.models if vendor is not None else [])
                if item.id == virtual.model
            ),
            None,
        )
        return {
            **virtual.model_dump(mode="json"),
            "vendor_label": vendor.label if vendor is not None else virtual.vendor,
            "model_label": model.label if model is not None else virtual.model,
            "used_by": sorted(
                feature
                for feature, binding in state.bindings.items()
                if binding.virtual == virtual.id
            ),
        }

    def _feature_view(self, feature: str, state: AIState) -> dict[str, Any]:
        spec = FEATURE_BY_KEY[feature]
        binding = state.bindings.get(feature)
        virtual = (
            state.virtual_model(binding.virtual)
            if binding is not None and binding.virtual
            else None
        )
        vendor_id = (
            virtual.vendor
            if virtual is not None
            else (binding.vendor if binding is not None else "")
        )
        model = (
            virtual.model
            if virtual is not None
            else (binding.model if binding is not None else "")
        )
        vendor = state.vendor(vendor_id) if vendor_id else None
        integration = (
            self._internal_integration(state, vendor.id, spec.kinds)
            if vendor is not None
            else None
        )
        return {
            "key": feature,
            "label": spec.label,
            "description": spec.description,
            "kinds": list(spec.kinds),
            "requires_model": spec.requires_model,
            "vendor": vendor.id if vendor is not None else "",
            "vendor_label": vendor.label if vendor is not None else "",
            "model": model if vendor is not None else "",
            # The alias the console edits, or empty for a direct binding. The
            # resolved supplier and model above follow it, so a reader does not
            # have to look the alias up itself.
            "virtual": virtual.id if virtual is not None else "",
            "virtual_label": virtual.label if virtual is not None else "",
            "source": "admin" if vendor is not None else "none",
            # Kept for diagnostics and older callers; the console binds by model.
            "integration_id": integration.id if integration is not None else None,
            "integration_name": integration.name if integration is not None else "",
        }

    # -- writes ----------------------------------------------------------

    def _ensure_vendor(
        self, state: AIState, vendor_id: str, *, create: bool
    ) -> VendorRecord:
        vendor = state.vendor(vendor_id)
        if vendor is not None:
            return vendor
        if not create:
            raise AINotFound(f"supplier not found: {vendor_id}")
        vendor = VendorRecord(id=vendor_id, label=vendor_label_for(vendor_id))
        state.vendors.append(vendor)
        return vendor

    def create_vendor(self, draft: VendorDraft) -> VendorRecord:
        with self._lock:
            state = self.state().model_copy(deep=True)
            if state.vendor(draft.id) is not None:
                raise AIConflict(f"a supplier named {draft.id!r} already exists")
            vendor = VendorRecord(**draft.model_dump())
            state.vendors.append(vendor)
            self._commit(state)
            return vendor

    def update_vendor(self, vendor_id: str, draft: VendorDraft) -> VendorRecord:
        with self._lock:
            state = self.state().model_copy(deep=True)
            if draft.id != vendor_id:
                raise AIProviderError("supplier id in the body does not match the path")
            index = next(
                (
                    position
                    for position, vendor in enumerate(state.vendors)
                    if vendor.id == vendor_id
                ),
                None,
            )
            if index is None:
                raise AINotFound(f"supplier not found: {vendor_id}")
            old = state.vendors[index]
            removed = {
                model.id for model in old.models
            } - {model.id for model in draft.models}
            if removed:
                for integration in state.integrations:
                    if integration.vendor != vendor_id:
                        continue
                    if integration.default_model in removed:
                        raise AIProviderError(
                            f"cannot remove model {integration.default_model!r}: "
                            f"it is the default for {integration.name!r}"
                        )
                for virtual in state.virtual_models:
                    if virtual.vendor == vendor_id and virtual.model in removed:
                        raise AIProviderError(
                            f"cannot remove model {virtual.model!r}: virtual "
                            f"model {virtual.id!r} maps to it"
                        )
                for feature, binding in state.bindings.items():
                    if binding.model not in removed:
                        continue
                    if binding.vendor == vendor_id:
                        raise AIProviderError(
                            f"cannot remove model {binding.model!r}: "
                            f"{feature} is bound to it"
                        )
                    if binding.integration_id:
                        integration = state.integration(binding.integration_id)
                        if integration is not None and integration.vendor == vendor_id:
                            raise AIProviderError(
                                f"cannot remove model {binding.model!r}: "
                                f"{feature} is bound to it"
                            )
            merged = old.model_dump()
            merged.update(draft.model_dump())
            merged["updated_at"] = utcnow()
            vendor = VendorRecord.model_validate(merged)
            state.vendors[index] = vendor
            self._commit(state)
            return vendor

    def create_integration(self, draft: IntegrationDraft) -> IntegrationRecord:
        with self._lock:
            state = self.state().model_copy(deep=True)
            if any(item.name == draft.name for item in state.integrations):
                raise AIConflict(f"an integration named {draft.name!r} already exists")
            vendor_id = draft.vendor or infer_vendor(draft.name, draft.base_url)
            vendor = self._ensure_vendor(state, vendor_id, create=not draft.vendor)
            merged = draft.model_dump()
            merged["vendor"] = vendor.id
            record = IntegrationRecord(**merged)
            state.integrations.append(record)
            if (
                record.kind != "fake"
                and record.default_model
                and not any(model.id == record.default_model for model in vendor.models)
            ):
                vendor.models.append(ModelRecord(id=record.default_model))
            self._commit(state)
            return record

    def update_integration(
        self, integration_id: str, draft: IntegrationDraft
    ) -> IntegrationRecord:
        with self._lock:
            state = self.state().model_copy(deep=True)
            index = next(
                (
                    position
                    for position, item in enumerate(state.integrations)
                    if item.id == integration_id
                ),
                None,
            )
            if index is None:
                raise AINotFound(f"integration not found: {integration_id}")
            if any(
                item.name == draft.name and item.id != integration_id
                for item in state.integrations
            ):
                raise AIConflict(f"an integration named {draft.name!r} already exists")
            existing = state.integrations[index]
            vendor_id = draft.vendor or existing.vendor
            vendor = self._ensure_vendor(state, vendor_id, create=False)
            merged = existing.model_dump()
            merged.update(draft.model_dump())
            merged["vendor"] = vendor.id
            merged["updated_at"] = utcnow()
            record = IntegrationRecord.model_validate(merged)
            prospective = [
                record if item.id == integration_id else item
                for item in state.integrations
            ]
            for feature, binding in state.bindings.items():
                spec = FEATURE_BY_KEY[feature]
                virtual = (
                    state.virtual_model(binding.virtual) if binding.virtual else None
                )
                binding_vendor = virtual.vendor if virtual is not None else binding.vendor
                if not binding_vendor and binding.integration_id:
                    legacy = state.integration(binding.integration_id)
                    binding_vendor = legacy.vendor if legacy is not None else ""
                if binding_vendor != record.vendor:
                    continue
                candidates = [
                    item
                    for item in prospective
                    if item.vendor == record.vendor
                    and item.enabled
                    and item.kind in spec.kinds
                ]
                if not candidates:
                    raise AIProviderError(
                        f"cannot change {record.name!r} to {record.kind}: "
                        f"{feature} uses supplier {record.vendor!r}; add a "
                        "compatible internal endpoint first"
                    )
                if record.kind != "fake" and spec.requires_model:
                    # A virtual binding already names the model it needs; only
                    # a direct binding leans on the endpoint's default.
                    bound_model = virtual.model if virtual is not None else binding.model
                    if not (bound_model or record.default_model):
                        raise AIProviderError(
                            f"{feature} is bound to {record.name!r}; set a "
                            "model on the integration or the binding before "
                            "clearing the default model"
                        )
            if (
                record.kind != "fake"
                and record.default_model
                and not any(model.id == record.default_model for model in vendor.models)
            ):
                vendor.models.append(ModelRecord(id=record.default_model))
            state.integrations[index] = record
            self._commit(state)
            return record

    def delete_integration(self, integration_id: str) -> tuple[str, list[str]]:
        with self._lock:
            state = self.state().model_copy(deep=True)
            record = state.integration(integration_id)
            if record is None:
                raise AINotFound(f"integration not found: {integration_id}")
            state.integrations = [
                item for item in state.integrations if item.id != integration_id
            ]
            cleared: list[str] = []
            for feature, binding in state.bindings.items():
                spec = FEATURE_BY_KEY[feature]
                virtual = (
                    state.virtual_model(binding.virtual) if binding.virtual else None
                )
                vendor_id = virtual.vendor if virtual is not None else binding.vendor
                if binding.integration_id == integration_id:
                    state.bindings[feature] = BindingRecord()
                    cleared.append(feature)
                elif vendor_id == record.vendor and self._internal_integration(
                    state, vendor_id, spec.kinds
                ) is None:
                    # The supplier has no internal endpoint left for this
                    # feature, so the binding could not resolve anyway. Clear
                    # the alias too: an empty binding has no target.
                    state.bindings[feature] = BindingRecord()
                    cleared.append(feature)
            self._commit(state)
            return record.name, sorted(cleared)

    def create_virtual_model(
        self, draft: VirtualModelDraft
    ) -> VirtualModelRecord:
        with self._lock:
            state = self.state().model_copy(deep=True)
            if state.virtual_model(draft.id) is not None:
                raise AIConflict(
                    f"a virtual model named {draft.id!r} already exists"
                )
            record = VirtualModelRecord(**draft.model_dump())
            state.virtual_models.append(record)
            self._commit(state)
            return record

    def update_virtual_model(
        self, virtual_id: str, draft: VirtualModelDraft
    ) -> VirtualModelRecord:
        with self._lock:
            state = self.state().model_copy(deep=True)
            if draft.id != virtual_id:
                raise AIProviderError(
                    "virtual model id in the body does not match the path"
                )
            index = next(
                (
                    position
                    for position, item in enumerate(state.virtual_models)
                    if item.id == virtual_id
                ),
                None,
            )
            if index is None:
                raise AINotFound(f"virtual model not found: {virtual_id}")
            merged = state.virtual_models[index].model_dump()
            merged.update(draft.model_dump())
            merged["updated_at"] = utcnow()
            record = VirtualModelRecord.model_validate(merged)
            state.virtual_models[index] = record
            # Every feature bound to the alias follows the new target on its
            # next call; the commit rejects a target with no usable endpoint.
            self._commit(state)
            return record

    def delete_virtual_model(self, virtual_id: str) -> tuple[str, list[str]]:
        with self._lock:
            state = self.state().model_copy(deep=True)
            if state.virtual_model(virtual_id) is None:
                raise AINotFound(f"virtual model not found: {virtual_id}")
            used_by = sorted(
                feature
                for feature, binding in state.bindings.items()
                if binding.virtual == virtual_id
            )
            if used_by:
                raise AIConflict(
                    f"virtual model {virtual_id!r} is used by "
                    f"{', '.join(used_by)}; point those features at another "
                    "virtual model first"
                )
            state.virtual_models = [
                item for item in state.virtual_models if item.id != virtual_id
            ]
            self._commit(state)
            return virtual_id, used_by

    def _check_binding_target(
        self,
        state: AIState,
        feature: str,
        spec: Any,
        vendor_id: str,
        model: str,
    ) -> None:
        """The supplier must be reachable for this feature and name the model."""
        vendor = state.vendor(vendor_id)
        if vendor is None:
            raise AINotFound(f"supplier not found: {vendor_id}")
        integration = self._internal_integration(state, vendor.id, spec.kinds)
        if integration is None:
            raise AIProviderError(
                f"supplier {vendor.label!r} has no enabled internal endpoint "
                f"for {feature}"
            )
        if vendor.id == "fake":
            return
        if not model:
            raise AIProviderError(
                f"choose a model for {feature} from {vendor.label!r}"
            )
        if not any(item.id == model for item in vendor.models):
            raise AIProviderError(
                f"model {model!r} is not in the {vendor.label} catalog; "
                "sync the supplier models first"
            )

    def _binding_record(
        self, state: AIState, feature: str, draft: BindingDraft
    ) -> BindingRecord:
        """Turn one draft into the binding shape the state stores.

        An explicitly empty draft unbinds the feature. A draft with no field at
        all is a caller mistake and is rejected, because "clear this feature" has
        to be stated rather than implied by an empty body. A ``virtual`` draft is
        stored as the alias alone, so moving the alias is enough to move every
        feature that uses it; ``vendor`` + ``model`` remains the direct shape for
        API callers.
        """
        spec = FEATURE_BY_KEY[feature]
        if draft.virtual:
            if draft.vendor or draft.model:
                raise AIProviderError(
                    "bind a feature to either a virtual model or a supplier "
                    "model, not both"
                )
            virtual = state.virtual_model(draft.virtual)
            if virtual is None:
                raise AINotFound(f"virtual model not found: {draft.virtual}")
            self._check_binding_target(
                state, feature, spec, virtual.vendor, virtual.model
            )
            return BindingRecord(virtual=virtual.id)
        vendor_id = draft.vendor
        model = draft.model
        if not vendor_id and draft.integration_id:
            # Compatibility with the v3 API shape: derive the supplier from
            # the endpoint the caller named.
            legacy = state.integration(draft.integration_id)
            if legacy is None:
                raise AINotFound(f"integration not found: {draft.integration_id}")
            vendor_id = legacy.vendor
            model = model or legacy.default_model
        if not vendor_id:
            if not draft.model_fields_set:
                raise AIProviderError(
                    f"choose a model for {feature} in Admin -> AI suppliers"
                )
            return BindingRecord()
        vendor = state.vendor(vendor_id)
        if vendor is None:
            raise AINotFound(f"supplier not found: {vendor_id}")
        integration = self._internal_integration(state, vendor.id, spec.kinds)
        if integration is None:
            raise AIProviderError(
                f"supplier {vendor.label!r} has no enabled internal endpoint "
                f"for {feature}"
            )
        if vendor.id == "fake":
            model = "fake"
        else:
            model = model or integration.default_model
        self._check_binding_target(state, feature, spec, vendor.id, model)
        return BindingRecord(vendor=vendor.id, model=model)

    def set_binding(self, feature: str, draft: BindingDraft) -> BindingRecord:
        if feature not in FEATURE_BY_KEY:
            raise AINotFound(f"unknown AI feature: {feature}")
        with self._lock:
            state = self.state().model_copy(deep=True)
            binding = self._binding_record(state, feature, draft)
            state.bindings[feature] = binding
            self._commit(state)
            return binding

    def set_bindings(
        self, drafts: dict[str, BindingDraft]
    ) -> dict[str, BindingRecord]:
        """Apply several feature bindings in one write.

        The whole batch is validated against one candidate state, so a single
        bad row leaves every binding untouched — the console's one save button
        either applies the table or explains why it cannot.
        """
        saved: dict[str, BindingRecord] = {}
        with self._lock:
            state = self.state().model_copy(deep=True)
            for feature, draft in drafts.items():
                if feature not in FEATURE_BY_KEY:
                    raise AINotFound(f"unknown AI feature: {feature}")
                binding = self._binding_record(state, feature, draft)
                state.bindings[feature] = binding
                saved[feature] = binding
            self._commit(state)
            return saved


_VERSIONED_BASE = re.compile(r"/v\d+$")


def _openai_base(value: str) -> str:
    """The API root to append ``/chat/completions`` or ``/models`` to.

    A base that already ends in a version segment is the root: ``/v1`` for
    OpenAI, DeepSeek and DashScope, ``/v4`` for Zhipu. Anything else gets
    ``/v1``, which is the OpenAI-compatible convention.
    """
    base = value.strip().rstrip("/")
    if _VERSIONED_BASE.search(base):
        return base
    return f"{base}/v1"


def _probe_target(state: AIState, vendor_id: str) -> tuple[str, str] | None:
    """The URL and protocol that answer "does this supplier's key work?".

    An internal OpenAI endpoint comes first because it is the one internal
    calls use; a gateway-only endpoint is still probeable, which covers a
    supplier that serves OpenAI-compatible traffic only. Anthropic answers the
    same ``GET /models`` shape with a header of its own. A speech-only supplier
    has no cheap probe, and the caller reports that instead of guessing.
    """
    candidates = [
        integration
        for integration in state.integrations
        if integration.vendor == vendor_id
        and integration.enabled
        and integration.base_url
        and integration.kind in ("openai", "anthropic")
    ]
    candidates.sort(
        key=lambda item: (
            0 if (item.kind == "openai" and not item.gateway) else 1,
            0 if item.kind == "openai" else 1,
        )
    )
    for integration in candidates:
        base = integration.base_url.strip().rstrip("/")
        if not base:
            continue
        if integration.kind == "openai":
            return f"{_openai_base(base)}/models", "openai"
        root = base if _VERSIONED_BASE.search(base) else f"{base}/v1"
        return f"{root}/models", "anthropic"
    return None


def _probe_headers(protocol: str, api_key: str) -> dict[str, str]:
    """The credential headers the supplier itself expects.

    Anthropic does not read ``Authorization``; sending a bearer token there
    would make every check report the key as rejected.
    """
    if protocol == "anthropic":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {api_key}"}


def _excerpt(response: httpx.Response, limit: int = 300) -> str:
    """One short line from the supplier's error body, for the console.

    The body may be an HTML error page with newlines and scripts; the console
    shows a single line, so it is collapsed and cut. A response body never
    contains the key that was sent.
    """
    text = " ".join((response.text or "").split())
    if not text:
        return response.reason_phrase or f"HTTP {response.status_code}"
    return text[:limit] + ("…" if len(text) > limit else "")


def _volcengine_config(provider: ResolvedProvider) -> VolcengineASRConfig:
    options = dict(provider.options)
    if "base_url" in options:
        # Older seed files may carry it here; the integration field wins.
        options.pop("base_url")
    merged = {
        "base_url": provider.base_url or VolcengineASRConfig().base_url,
        **options,
    }
    try:
        return VolcengineASRConfig.model_validate(merged)
    except Exception as exc:  # noqa: BLE001 - pydantic's message is the detail
        raise AIProviderError(
            f"invalid Volcengine ASR options on {provider.integration_name!r}: {exc}"
        ) from exc
