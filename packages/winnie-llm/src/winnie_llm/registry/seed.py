"""Seed the supplier registry and migrate old files.

The registry is the only source of provider choice. The host passes a
:class:`SeedInput` built from its own configuration (gateway routes, the
Volcengine ASR defaults and the first-start ASR binding); this module knows no
host settings type.

A fresh registry binds only the ASR feature when the host asks for it. Every
model feature stays unbound until the owner adds a supplier API, syncs its
models and binds one in the console. No fake supplier ships.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from winnie_llm.asr.config import VolcengineASRConfig
from winnie_llm.registry.schema import (
    AIState,
    BindingRecord,
    IntegrationRecord,
    ModelRecord,
    VendorRecord,
    infer_vendor,
    utcnow,
    vendor_label_for,
)

_VENDOR_LABELS = {
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "qwen": "Qwen",
    "volcengine": "Volcengine",
}


@dataclass(frozen=True)
class SeedRoute:
    """One gateway route the host's configuration already knows about."""

    name: str
    protocol: Literal["openai", "anthropic"]
    base_url: str


@dataclass(frozen=True)
class SeedInput:
    """Host configuration that seeds a missing registry file."""

    routes: tuple[SeedRoute, ...] = ()
    asr: VolcengineASRConfig = field(default_factory=VolcengineASRConfig)
    bind_asr: bool = False


def _route_label(name: str) -> str:
    """A readable label; protocol suffixes remain visible, not a second vendor."""
    parts = name.split("-")
    if len(parts) > 1 and parts[-1] in {"openai", "anthropic"}:
        vendor = "-".join(parts[:-1])
        vendor_label = _VENDOR_LABELS.get(vendor, vendor.replace("-", " ").title())
        protocol = (
            "OpenAI-compatible" if parts[-1] == "openai" else "Anthropic-compatible"
        )
        return f"{vendor_label} ({protocol})"
    return _VENDOR_LABELS.get(name, name.replace("-", " ").title())


def _unique_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    index = 2
    while f"{name}-{index}" in taken:
        index += 1
    return f"{name}-{index}"


def seed_state(seed: SeedInput) -> AIState:
    """Build the first registry from the host's remaining configuration."""
    integrations: list[IntegrationRecord] = []
    vendors: dict[str, VendorRecord] = {}
    taken: set[str] = set()

    def ensure_vendor(vendor_id: str) -> VendorRecord:
        vendor = vendors.get(vendor_id)
        if vendor is None:
            vendor = VendorRecord(
                id=vendor_id,
                label=vendor_label_for(vendor_id),
            )
            vendors[vendor_id] = vendor
        return vendor

    def add_model(vendor_id: str, model_id: str) -> None:
        if not model_id:
            return
        vendor = ensure_vendor(vendor_id)
        if not any(model.id == model_id for model in vendor.models):
            vendor.models.append(ModelRecord(id=model_id))

    def add(**values: object) -> IntegrationRecord:
        name = _unique_name(str(values.pop("name")), taken)
        taken.add(name)
        base_url = str(values.get("base_url") or "")
        vendor_id = str(values.pop("vendor", "") or infer_vendor(name, base_url))
        ensure_vendor(vendor_id)
        record = IntegrationRecord(
            name=name, vendor=vendor_id, **values  # type: ignore[arg-type]
        )
        integrations.append(record)
        add_model(vendor_id, record.default_model)
        return record

    for route in seed.routes:
        add(
            name=route.name,
            label=_route_label(route.name),
            kind=route.protocol,
            base_url=route.base_url,
            # A gateway route is called with a Winnie key; the host injects the
            # supplier credential from the vendor record on the way upstream.
            gateway=True,
        )

    # The endpoint/options still have useful defaults in the host configuration,
    # but the ASR provider is chosen on the AI suppliers page; only the binding
    # below uses the legacy asr_provider value as a first-start default.
    asr_options = seed.asr.model_dump()
    asr_options.pop("base_url", None)
    asr = add(
        name="volcengine-asr",
        label="Volcengine Doubao ASR",
        kind="volcengine_asr",
        base_url=seed.asr.base_url,
        default_model=seed.asr.model_name,
        gateway=False,
        options=asr_options,
    )

    bindings: dict[str, BindingRecord] = {}
    if seed.bind_asr:
        bindings["meetings.transcribe"] = BindingRecord(
            vendor=asr.vendor,
            model=asr.default_model or "bigmodel",
        )

    return AIState(
        version=7,
        vendors=list(vendors.values()),
        integrations=integrations,
        bindings=bindings,
    )


def _add_vendors(state: AIState) -> None:
    """Group v2 endpoint records under suppliers and collect their models."""
    vendors: dict[str, VendorRecord] = {vendor.id: vendor for vendor in state.vendors}
    for integration in state.integrations:
        vendor_id = integration.vendor or infer_vendor(
            integration.name, integration.base_url
        )
        vendor = vendors.get(vendor_id)
        if vendor is None:
            vendor = VendorRecord(
                id=vendor_id,
                label=vendor_label_for(vendor_id),
            )
            vendors[vendor_id] = vendor
        integration.vendor = vendor_id
        if integration.kind == "fake" or not integration.default_model:
            continue
        if not any(model.id == integration.default_model for model in vendor.models):
            vendor.models.append(ModelRecord(id=integration.default_model))

    by_id = {integration.id: integration for integration in state.integrations}
    for binding in state.bindings.values():
        if not binding.integration_id or not binding.model:
            continue
        integration = by_id.get(binding.integration_id)
        if integration is None or integration.kind == "fake":
            continue
        vendor = vendors[integration.vendor]
        if not any(model.id == binding.model for model in vendor.models):
            vendor.models.append(ModelRecord(id=binding.model))

    state.vendors = list(vendors.values())


def migrate_state(
    state: AIState, legacy_keys: dict[str, str] | None = None
) -> bool:
    """Upgrade an old registry in place; return whether it must be written back.

    ``legacy_keys`` maps an integration name to the ``api_key_env`` an older
    file stored there. Version 6 removed that field, so the value is only
    needed to recognise a v1 file's combined internal route. Version 7 stores
    each supplier's key on its record; the caller imports the env backup at
    that point because this function has no access to the file.
    """
    legacy_keys = legacy_keys or {}
    changed = False
    if state.version < 2:
        for integration in state.integrations:
            if integration.kind not in {"openai", "anthropic"}:
                integration.gateway = False
            # Only replace the label the old seed generated; keep manual edits.
            if integration.label == integration.name.replace("-", " ").title():
                label = _route_label(integration.name)
                if label != integration.label:
                    integration.label = label

        referenced = {
            binding.integration_id
            for binding in state.bindings.values()
            if binding.integration_id is not None
        }
        combined = next(
            (
                integration
                for integration in state.integrations
                if integration.kind == "openai"
                and integration.id in referenced
                and legacy_keys.get(integration.name, "")
            ),
            None,
        )
        if combined is not None:
            internal = IntegrationRecord(
                name=_unique_name(
                    "internal-llm",
                    {integration.name for integration in state.integrations},
                ),
                label="Internal LLM",
                kind="openai",
                base_url=combined.base_url,
                default_model=combined.default_model,
                enabled=combined.enabled,
                gateway=False,
            )
            combined.default_model = ""
            combined.gateway = True
            combined.updated_at = utcnow()
            for binding in state.bindings.values():
                if binding.integration_id == combined.id:
                    binding.integration_id = internal.id
                    binding.updated_at = utcnow()
            state.integrations.append(internal)
        state.version = 2
        changed = True

    if state.version < 3:
        _add_vendors(state)
        state.version = 3
        changed = True

    legacy_bindings = any(
        binding.integration_id for binding in state.bindings.values()
    )
    if state.version < 4 or legacy_bindings:
        by_id = {integration.id: integration for integration in state.integrations}
        for binding in state.bindings.values():
            if binding.vendor:
                binding.integration_id = None
                continue
            if not binding.integration_id:
                continue
            integration = by_id.get(binding.integration_id)
            if integration is None:
                continue
            binding.vendor = integration.vendor
            model = binding.model or integration.default_model
            if integration.kind == "fake":
                model = "fake"
            binding.model = model
            binding.integration_id = None
            binding.updated_at = utcnow()
        state.version = 4
        changed = True

    if state.version < 5:
        state.integrations = [
            integration
            for integration in state.integrations
            if integration.kind != "fake"
        ]
        state.vendors = [vendor for vendor in state.vendors if vendor.id != "fake"]
        valid_vendors = {vendor.id for vendor in state.vendors}
        for binding in state.bindings.values():
            if binding.vendor == "fake" or (
                binding.vendor and binding.vendor not in valid_vendors
            ):
                binding.vendor = ""
                binding.model = ""
                binding.integration_id = None
                binding.updated_at = utcnow()
        state.version = 5
        changed = True

    if state.version < 6:
        # The file no longer stores a key variable name; the loader already
        # stripped it before validation. Bump the version so the cleanup is
        # written back once.
        state.version = 6
        changed = True

    return changed
