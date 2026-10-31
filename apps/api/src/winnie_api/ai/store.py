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
    ResolvedProvider,
    utcnow,
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

    def resolve(self, feature: str) -> ResolvedProvider:
        spec = FEATURE_BY_KEY.get(feature)
        if spec is None:
            raise AINotFound(f"unknown AI feature: {feature}")
        state = self.state()
        binding = state.bindings.get(feature)
        if binding is not None and binding.integration_id:
            integration = state.integration(binding.integration_id)
            if integration is None:
                raise AIProviderError(
                    f"feature {feature} references missing integration "
                    f"{binding.integration_id}"
                )
            if not integration.enabled:
                raise AIProviderError(
                    f"integration {integration.name!r} is disabled; enable it in "
                    "Admin -> AI providers"
                )
            if integration.kind not in spec.kinds:
                raise AIProviderError(
                    f"feature {feature} cannot use {integration.kind!r} integration "
                    f"{integration.name!r}"
                )
            model = binding.model
            if integration.kind != "fake" and spec.requires_model:
                model = model or integration.default_model
            if integration.kind == "fake":
                model = ""
            return ResolvedProvider(
                feature=feature,
                source="admin",
                integration_id=integration.id,
                integration_name=integration.name,
                kind=integration.kind,
                base_url=integration.base_url,
                api_key_env=integration.api_key_env,
                model=model,
                options=dict(integration.options),
            )
        raise AIProviderError(
            f"feature {feature} has no provider; choose one in "
            "Admin -> AI providers"
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

    def view(self) -> dict[str, Any]:
        state = self.state()
        return {
            "path": str(self.path),
            "runtime": state.runtime.model_dump(mode="json"),
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
                [integration.updated_at for integration in state.integrations]
                + [binding.updated_at for binding in state.bindings.values()],
                default="",
            ),
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
        return {
            **integration.model_dump(mode="json"),
            "kind_label": kind.label,
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
        integration = (
            state.integration(binding.integration_id)
            if binding is not None and binding.integration_id
            else None
        )
        return {
            "key": feature,
            "label": spec.label,
            "description": spec.description,
            "kinds": list(spec.kinds),
            "requires_model": spec.requires_model,
            "integration_id": binding.integration_id if binding is not None else None,
            "model": binding.model if binding is not None else "",
            "source": "admin" if integration is not None else "none",
            "integration_name": integration.name if integration is not None else "",
        }

    # -- writes ----------------------------------------------------------

    def create_integration(self, draft: IntegrationDraft) -> IntegrationRecord:
        with self._lock:
            state = self.state().model_copy(deep=True)
            if any(item.name == draft.name for item in state.integrations):
                raise AIConflict(f"an integration named {draft.name!r} already exists")
            record = IntegrationRecord(**draft.model_dump())
            state.integrations.append(record)
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
            merged = state.integrations[index].model_dump()
            merged.update(draft.model_dump())
            merged["updated_at"] = utcnow()
            record = IntegrationRecord.model_validate(merged)
            for feature, binding in state.bindings.items():
                if binding.integration_id != integration_id:
                    continue
                spec = FEATURE_BY_KEY[feature]
                if record.kind not in spec.kinds:
                    raise AIProviderError(
                        f"cannot change {record.name!r} to {record.kind}: "
                        f"{feature} is bound to it; rebind the feature first"
                    )
                if record.kind != "fake" and spec.requires_model:
                    if not (binding.model or record.default_model):
                        raise AIProviderError(
                            f"{feature} is bound to {record.name!r}; set a "
                            "model on the integration or the binding before "
                            "clearing the default model"
                        )
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
                if binding.integration_id == integration_id:
                    binding.integration_id = None
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
            integration: IntegrationRecord | None = None
            if draft.integration_id:
                integration = state.integration(draft.integration_id)
                if integration is None:
                    raise AINotFound(
                        f"integration not found: {draft.integration_id}"
                    )
                if not integration.enabled:
                    raise AIProviderError(
                        f"integration {integration.name!r} is disabled"
                    )
                if integration.kind not in spec.kinds:
                    raise AIProviderError(
                        f"feature {feature} cannot use a {integration.kind} integration"
                    )
                if integration.kind == "openai" and not integration.api_key_env:
                    raise AIProviderError(
                        f"integration {integration.name!r} is gateway-only; set "
                        f"api_key_env before binding {feature}"
                    )

            model = draft.model
            if integration is None or integration.kind == "fake":
                model = ""
            elif spec.requires_model:
                model = model or integration.default_model
                if not model:
                    raise AIProviderError(
                        f"choose a model for {feature}: the integration has no "
                        "default_model"
                    )
            else:
                model = ""

            binding = BindingRecord(
                integration_id=integration.id if integration is not None else None,
                model=model,
            )
            state.bindings[feature] = binding
            self._commit(state)
            return binding
