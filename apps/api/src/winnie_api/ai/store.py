"""Read, validate and persist the admin-managed AI registry.

One small YAML file keeps the state. It is read on demand (a changed mtime is
picked up automatically, so an arq worker sees a console change without a
restart) and written atomically. The file holds no secrets: ``api_key_env`` is
the *name* of an environment variable, never a key.
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import ValidationError

from winnie_gateway.routes import Route

from winnie_api.ai.errors import AIConflict, AINotFound, AIProviderError
from winnie_api.ai.schema import (
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
    VendorDraft,
    VendorRecord,
    infer_vendor,
    utcnow,
    vendor_label_for,
)
from winnie_api.ai.seed import migrate_state, seed_state

from winnie_core.config import ModelRuntimeConfig

_HEADER = (
    "# Winnie AI providers and feature bindings.\n"
    "# Written by the admin console at /api/v1/ai; use the console rather than\n"
    "# editing this file. API keys are never stored here: api_key_env names an\n"
    "# environment variable that the API process reads at call time.\n"
)


class AISettingsService:
    """Own the file, expose CRUD, and resolve a feature to one provider."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.path: Path = settings.resolve_ai_path()
        self._lock = threading.RLock()
        self._state: AIState | None = None
        self._mtime_ns: int | None = None

    # -- lifecycle -------------------------------------------------------

    def load(self) -> None:
        """Load the file, or seed it from config.yaml on first start."""
        with self._lock:
            if not self.path.is_file():
                state = seed_state(self.settings)
                self._write(state)
                return
            self._read()

    def _read(self) -> None:
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise AIProviderError(
                f"cannot read AI settings at {self.path}: {exc}"
            ) from exc
        if raw is None:
            raise AIProviderError(f"AI settings file is empty: {self.path}")
        try:
            state = AIState.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - pydantic's message is the detail
            raise AIProviderError(
                f"invalid AI settings at {self.path}: {exc}"
            ) from exc
        if migrate_state(state):
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
            if integration.kind == "openai" and not integration.api_key_env:
                continue
            return integration
        return None

    def resolve(self, feature: str) -> ResolvedProvider:
        spec = FEATURE_BY_KEY.get(feature)
        if spec is None:
            raise AINotFound(f"unknown AI feature: {feature}")
        state = self.state()
        binding = state.bindings.get(feature)
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
            api_key_env=integration.api_key_env,
            model=model,
            options=dict(integration.options),
        )

    def route_table(self) -> dict[str, Route]:
        table: dict[str, Route] = {}
        for integration in self.state().integrations:
            if not integration.enabled:
                continue
            if integration.kind not in {"openai", "anthropic"}:
                continue
            if not integration.gateway:
                continue
            table[integration.name] = Route(
                name=integration.name,
                protocol=integration.kind,
                base_url=integration.base_url,
            )
        return table

    def runtime(self) -> ModelRuntimeConfig:
        return self.state().runtime

    def set_runtime(self, runtime: ModelRuntimeConfig) -> ModelRuntimeConfig:
        with self._lock:
            state = self.state().model_copy(deep=True)
            state.runtime = runtime
            self._commit(state)
            return runtime

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
        if not integration.base_url or not integration.api_key_env:
            raise AIProviderError(
                f"supplier {vendor.label!r} needs a base URL and API key variable"
            )
        api_key = os.environ.get(integration.api_key_env, "").strip()
        if not api_key:
            raise AIProviderError(
                f"environment variable {integration.api_key_env} is not set"
            )
        base = integration.base_url.rstrip("/")
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
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
        used = {
            binding.model
            for binding in state.bindings.values()
            if binding.vendor == vendor_id and binding.model
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

    def view(self) -> dict[str, Any]:
        state = self.state()
        return {
            "path": str(self.path),
            "runtime": state.runtime.model_dump(mode="json"),
            "vendors": [
                self._vendor_view(vendor, state) for vendor in state.vendors
            ],
            "integrations": [
                self._integration_view(integration, state)
                for integration in state.integrations
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
            ],
            "updated_at": max(
                [vendor.updated_at for vendor in state.vendors]
                + [integration.updated_at for integration in state.integrations]
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
        return {
            **vendor.model_dump(mode="json"),
            "integration_count": len(endpoints),
            "gateway_count": sum(1 for integration in endpoints if integration.gateway),
            "internal_integration_id": internal.id if internal is not None else "",
            "kind": internal.kind if internal is not None else "",
            "base_url": internal.base_url if internal is not None else "",
            "api_key_env": internal.api_key_env if internal is not None else "",
            "api_key_present": bool(
                internal is not None
                and internal.api_key_env
                and os.environ.get(internal.api_key_env, "").strip()
            ),
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
            "api_key_present": bool(
                integration.api_key_env
                and os.environ.get(integration.api_key_env, "").strip()
            ),
            "used_by": sorted(used_by),
            "gateway_paths": paths,
        }

    def _feature_view(self, feature: str, state: AIState) -> dict[str, Any]:
        spec = FEATURE_BY_KEY[feature]
        binding = state.bindings.get(feature)
        vendor = state.vendor(binding.vendor) if binding is not None else None
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
            "model": binding.model if binding is not None else "",
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
                binding_vendor = binding.vendor
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
                    and (item.kind != "openai" or item.api_key_env)
                ]
                if not candidates:
                    raise AIProviderError(
                        f"cannot change {record.name!r} to {record.kind}: "
                        f"{feature} uses supplier {record.vendor!r}; add a "
                        "compatible internal endpoint first"
                    )
                if record.kind != "fake" and spec.requires_model:
                    if not (binding.model or record.default_model):
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
                if binding.integration_id == integration_id:
                    binding.vendor = ""
                    binding.model = ""
                    binding.integration_id = None
                    binding.updated_at = utcnow()
                    cleared.append(feature)
                elif binding.vendor == record.vendor and self._internal_integration(
                    state, binding.vendor, spec.kinds
                ) is None:
                    binding.vendor = ""
                    binding.model = ""
                    binding.updated_at = utcnow()
                    cleared.append(feature)
            self._commit(state)
            return record.name, sorted(cleared)

    def set_binding(self, feature: str, draft: BindingDraft) -> BindingRecord:
        with self._lock:
            spec = FEATURE_BY_KEY.get(feature)
            if spec is None:
                raise AINotFound(f"unknown AI feature: {feature}")
            state = self.state().model_copy(deep=True)
            vendor_id = draft.vendor
            model = draft.model
            if not vendor_id and draft.integration_id:
                # Compatibility with the v3 API shape: derive the supplier
                # from the endpoint the caller named.
                legacy = state.integration(draft.integration_id)
                if legacy is None:
                    raise AINotFound(
                        f"integration not found: {draft.integration_id}"
                    )
                vendor_id = legacy.vendor
                model = model or legacy.default_model
            if not vendor_id:
                raise AIProviderError(
                    f"choose a model for {feature} in Admin -> AI suppliers"
                )
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
                if not model:
                    raise AIProviderError(
                        f"choose a model for {feature} from {vendor.label!r}"
                    )
                if not any(item.id == model for item in vendor.models):
                    raise AIProviderError(
                        f"model {model!r} is not in the {vendor.label} catalog; "
                        "sync the supplier models first"
                    )
            binding = BindingRecord(vendor=vendor.id, model=model)
            state.bindings[feature] = binding
            self._commit(state)
            return binding
