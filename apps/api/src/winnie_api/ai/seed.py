"""Seed the admin registry and migrate old files.

The registry is the only source of provider choice. ``config.yaml`` still
provides the gateway routes, the Volcengine ASR endpoint defaults and the
feature enable flags, but it no longer selects a model: a fresh registry binds
the model features to the deterministic fake until the owner adds a provider in
the console. No ``llm`` block exists in ``config.yaml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from winnie_meetings.integrations.asr.volcengine import API_KEY_ENV

from winnie_api.ai.schema import (
    AIState,
    BindingRecord,
    FEATURE_BY_KEY,
    IntegrationRecord,
    ModelRecord,
    VendorRecord,
    infer_vendor,
    utcnow,
    vendor_label_for,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle is avoided at runtime
    from winnie_api.settings import Settings

_VENDOR_LABELS = {
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "qwen": "Qwen",
    "volcengine": "Volcengine",
}


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


def seed_state(settings: Settings) -> AIState:
    """Build the first registry from the YAML configuration."""
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

    for name, route in settings.routes.items():
        add(
            name=name,
            label=_route_label(name),
            kind=route.protocol,
            base_url=route.base_url,
            # Gateway routes forward the client's key; no server-side key.
            api_key_env="",
            gateway=True,
        )

    # The endpoint/options still have useful defaults in YAML, but the ASR
    # provider is chosen on the AI providers page; only the binding below uses
    # the legacy asr_provider value as a first-start default.
    asr_config = settings.meetings.volcengine
    asr_options = asr_config.model_dump()
    asr_options.pop("base_url", None)
    asr = add(
        name="volcengine-asr",
        label="Volcengine Doubao ASR",
        kind="volcengine_asr",
        base_url=asr_config.base_url,
        api_key_env=API_KEY_ENV,
        default_model=asr_config.model_name,
        gateway=False,
        options=asr_options,
    )

    fake = add(
        name="fake",
        label="Deterministic fake (offline)",
        kind="fake",
        gateway=False,
    )

    bindings: dict[str, BindingRecord] = {}

    def bind(feature: str, integration: IntegrationRecord, model: str = "") -> None:
        bindings[feature] = BindingRecord(
            integration_id=integration.id,
            model=model,
        )

    # Model choice is admin-managed: every model feature starts on the fake
    # provider. The owner adds a real integration and rebinds it in the console.
    for feature in ("assistant.chat", "meetings.translate", "meetings.summarize"):
        bind(feature, fake)

    if settings.meetings.asr_provider == "volcengine":
        bind("meetings.transcribe", asr)
    else:
        bind("meetings.transcribe", fake)

    assert set(bindings) == set(FEATURE_BY_KEY)
    return AIState(
        version=3,
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


def migrate_state(state: AIState) -> bool:
    """Upgrade an old registry in place; return whether it must be written back.

    v1 reused the OpenAI route whose base URL matched ``llm.base_url`` as the
    internal LLM. That put the server-side key on a public gateway route and
    made one record serve two roles. v2 stores the internal LLM separately and
    adds an explicit ``gateway`` flag. v3 groups those endpoints under the
    supplier they belong to and records the models each supplier offers.
    """
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
                and integration.api_key_env
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
                api_key_env=combined.api_key_env,
                default_model=combined.default_model,
                enabled=combined.enabled,
                gateway=False,
            )
            combined.api_key_env = ""
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

    return changed
