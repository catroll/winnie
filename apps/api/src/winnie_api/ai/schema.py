"""Admin-managed AI supplier registry and the features that use it.

The registry is deliberately small: a supplier owns endpoints and a model
catalog, and a binding says which endpoint/model serves a feature. Gateway
routes are the endpoints with ``gateway: true``; internal jobs (assistant chat,
translation, minutes, ASR) resolve the endpoint named by their binding at call
time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from winnie_core.config import ModelRuntimeConfig

from winnie_meetings.config import VolcengineASRConfig

IntegrationKind = Literal["openai", "anthropic", "volcengine_asr", "fake"]

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A gateway route is a path prefix under the API root, so these names would
# collide with the host's own endpoints.
_RESERVED_NAMES = frozenset(
    {
        "api",
        "healthz",
        "news",
        "readyz",
        "routes",
        "admin",
        "docs",
        "openapi.json",
    }
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    label: str
    description: str
    kinds: tuple[IntegrationKind, ...]
    requires_model: bool


FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        key="assistant.chat",
        label="Assistant chat",
        description="The model behind the assistant's replies and tool calls.",
        kinds=("openai", "fake"),
        requires_model=True,
    ),
    FeatureSpec(
        key="meetings.transcribe",
        label="Meeting transcription",
        description="Speech-to-text for uploaded and live meeting audio.",
        kinds=("volcengine_asr", "fake"),
        requires_model=False,
    ),
    FeatureSpec(
        key="meetings.translate",
        label="Meeting translation",
        description="Translates non-local transcript segments into the meeting language.",
        kinds=("openai", "fake"),
        requires_model=True,
    ),
    FeatureSpec(
        key="meetings.summarize",
        label="Meeting minutes",
        description="Turns a transcript into structured minutes, decisions and action items.",
        kinds=("openai", "fake"),
        requires_model=True,
    ),
    FeatureSpec(
        key="news.extract",
        label="News extraction",
        description=(
            "Extracts facts and event cards from one news item, and reviews the "
            "gray-zone candidate matches that scoring could not decide."
        ),
        kinds=("openai", "fake"),
        requires_model=True,
    ),
    FeatureSpec(
        key="news.write",
        label="News writing",
        description="Writes one publishable draft from an event's coverage.",
        kinds=("openai", "fake"),
        requires_model=True,
    ),
    FeatureSpec(
        key="news.review",
        label="News review",
        description="Checks a draft against the material it was written from.",
        kinds=("openai", "fake"),
        requires_model=True,
    ),
)

FEATURE_BY_KEY: dict[str, FeatureSpec] = {feature.key: feature for feature in FEATURES}


@dataclass(frozen=True)
class KindSpec:
    kind: IntegrationKind
    label: str
    description: str
    gateway: bool
    requires_base_url: bool


KINDS: tuple[KindSpec, ...] = (
    KindSpec(
        kind="openai",
        label="OpenAI-compatible",
        description=(
            "A /chat/completions endpoint. Exposed as a gateway route and usable "
            "by the assistant, translation and minutes."
        ),
        gateway=True,
        requires_base_url=True,
    ),
    KindSpec(
        kind="anthropic",
        label="Anthropic",
        description=(
            "An Anthropic Messages endpoint. Exposed as a gateway route; internal "
            "jobs use OpenAI-compatible integrations."
        ),
        gateway=True,
        requires_base_url=True,
    ),
    KindSpec(
        kind="volcengine_asr",
        label="Volcengine Doubao ASR",
        description="Doubao speech-to-text for recorded and streaming meeting audio.",
        gateway=False,
        requires_base_url=True,
    ),
    KindSpec(
        kind="fake",
        label="Deterministic fake",
        description="Offline providers for tests and demos. They never call a vendor.",
        gateway=False,
        requires_base_url=False,
    ),
)

KIND_BY_KEY: dict[str, KindSpec] = {kind.kind: kind for kind in KINDS}


_VENDOR_LABELS = {
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "fake": "Deterministic fake",
    "openai": "OpenAI",
    "qwen": "Qwen",
    "volcengine": "Volcengine",
}

_VENDOR_HOSTS = (
    ("deepseek", "deepseek"),
    ("openai", "openai"),
    ("anthropic", "anthropic"),
    ("dashscope", "qwen"),
    ("qwen", "qwen"),
    ("volcengine", "volcengine"),
    ("bytedance", "volcengine"),
)


def vendor_label_for(vendor_id: str) -> str:
    return _VENDOR_LABELS.get(vendor_id, vendor_id.replace("-", " ").title())


def infer_vendor(name: str, base_url: str = "") -> str:
    """Group protocol/role records under the supplier they belong to."""
    host = (urlparse(base_url).hostname or "").lower()
    for needle, vendor in _VENDOR_HOSTS:
        if needle in host:
            return vendor

    slug = name.strip().lower()
    for suffix in ("-openai", "-anthropic", "-asr"):
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
            break
    for vendor in _VENDOR_LABELS:
        if slug == vendor:
            return vendor
    if slug.startswith("internal"):
        return "internal"
    cleaned = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")
    return cleaned or "custom"


def _validate_name(value: str) -> str:
    name = value.strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            "name must be lowercase letters, digits and dashes, start with a "
            "letter or digit, and be at most 63 characters"
        )
    if name in _RESERVED_NAMES:
        raise ValueError(f"name {name!r} is reserved by the API")
    return name


def _validate_env_name(value: str) -> str:
    name = value.strip()
    if not name:
        return ""
    if not _ENV_RE.fullmatch(name):
        raise ValueError(
            "api_key_env is the name of an environment variable, not the key "
            "itself (for example DEEPSEEK_API_KEY)"
        )
    return name


def _validate_url(value: str, *, kind: IntegrationKind) -> str:
    url = value.strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an absolute http(s) URL")
    if kind == "fake":
        raise ValueError("fake integrations do not use a base_url")
    return url


def _normalize_options(kind: IntegrationKind, options: dict[str, Any]) -> dict[str, Any]:
    if kind != "volcengine_asr":
        if options:
            raise ValueError(f"{kind} integrations do not accept options")
        return {}
    if "base_url" in options:
        raise ValueError("set base_url on the integration, not in options")
    # Reuse the capability's own model so types, ranges and defaults stay in
    # one place instead of being copied into the admin layer.
    validated = VolcengineASRConfig.model_validate(
        {"base_url": "https://placeholder.invalid", **options}
    )
    normalized = validated.model_dump()
    normalized.pop("base_url", None)
    return normalized


class ModelRecord(BaseModel):
    """One model id a supplier offers, with the metadata the console shows."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str = ""
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    notes: str = ""

    @field_validator("id", "label", "notes")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not value:
            raise ValueError("model id is required")
        if len(value) > 128:
            raise ValueError("model id must be at most 128 characters")
        return value

    @model_validator(mode="after")
    def _label(self) -> ModelRecord:
        if not self.label:
            self.label = self.id
        return self


class VendorDraft(BaseModel):
    """A supplier grouping endpoint integrations and the models they offer."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str = ""
    models: list[ModelRecord] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        name = value.strip()
        if not _NAME_RE.fullmatch(name):
            raise ValueError(
                "vendor id must be lowercase letters, digits and dashes, start "
                "with a letter or digit, and be at most 63 characters"
            )
        return name

    @field_validator("label")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _models(self) -> VendorDraft:
        seen: set[str] = set()
        for model in self.models:
            if model.id in seen:
                raise ValueError(f"duplicate model id: {model.id}")
            seen.add(model.id)
        if not self.label:
            self.label = vendor_label_for(self.id)
        return self


class VendorRecord(VendorDraft):
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class IntegrationInput(BaseModel):
    """The fields a console user can change on an integration."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str = ""
    # Supplier grouping. Store methods fill it in for older API clients; a
    # version 3 file must carry it explicitly.
    vendor: str = ""
    kind: IntegrationKind
    base_url: str = ""
    api_key_env: str = ""
    default_model: str = ""
    enabled: bool = True
    # Whether an OpenAI/Anthropic endpoint is exposed as a gateway route.
    # Internal-only endpoints keep False.
    gateway: bool = True
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("vendor")
    @classmethod
    def _vendor(cls, value: str) -> str:
        name = value.strip()
        if not name:
            return ""
        if not _NAME_RE.fullmatch(name):
            raise ValueError(
                "vendor must be lowercase letters, digits and dashes, start "
                "with a letter or digit, and be at most 63 characters"
            )
        return name

    @field_validator("api_key_env")
    @classmethod
    def _env(cls, value: str) -> str:
        return _validate_env_name(value)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str, info: Any) -> str:
        # ``kind`` is declared before ``base_url``, so it is available here.
        return _validate_url(value, kind=info.data.get("kind", "openai"))

    @field_validator("label", "default_model")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _kind_fields(self) -> IntegrationInput:
        spec = KIND_BY_KEY[self.kind]
        if spec.requires_base_url and not self.base_url:
            raise ValueError(f"{spec.label} requires a base_url")
        if self.kind == "fake" and (self.api_key_env or self.default_model):
            raise ValueError("fake integrations do not use an API key or model")
        if not spec.gateway:
            self.gateway = False
        self.options = _normalize_options(self.kind, self.options)
        return self


class IntegrationRecord(IntegrationInput):
    """A stored integration, with identity and timestamps."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class IntegrationDraft(IntegrationInput):
    """The request body for creating or updating an integration."""


class BindingRecord(BaseModel):
    """Which integration (and optional model override) serves one feature."""

    model_config = ConfigDict(extra="forbid")

    integration_id: str | None = None
    model: str = ""
    updated_at: str = Field(default_factory=utcnow)

    @field_validator("model")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class BindingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integration_id: str | None = None
    model: str = ""

    @field_validator("model")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class AIState(BaseModel):
    """The whole file: suppliers, endpoints, models and feature bindings."""

    model_config = ConfigDict(extra="forbid")

    version: int = 2
    # Shared tuning only; provider base URL/model/key belong to an integration.
    runtime: ModelRuntimeConfig = Field(default_factory=ModelRuntimeConfig)
    # Supplier -> endpoints -> models. The same vendor can contribute several
    # endpoint records (protocol/role), but it is one supplier in the console.
    vendors: list[VendorRecord] = Field(default_factory=list)
    integrations: list[IntegrationRecord] = Field(default_factory=list)
    bindings: dict[str, BindingRecord] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> AIState:
        if self.version not in (1, 2, 3, 4):
            raise ValueError(f"unsupported ai settings version: {self.version}")

        vendor_ids: set[str] = set()
        for vendor in self.vendors:
            if vendor.id in vendor_ids:
                raise ValueError(f"duplicate vendor id: {vendor.id}")
            vendor_ids.add(vendor.id)

        ids: set[str] = set()
        names: set[str] = set()
        for integration in self.integrations:
            if integration.id in ids:
                raise ValueError(f"duplicate integration id: {integration.id}")
            if integration.name in names:
                raise ValueError(f"duplicate integration name: {integration.name}")
            ids.add(integration.id)
            names.add(integration.name)

        by_id = {integration.id: integration for integration in self.integrations}
        vendors_by_id = {vendor.id: vendor for vendor in self.vendors}
        if self.version >= 3:
            for integration in self.integrations:
                if not integration.vendor:
                    raise ValueError(
                        f"integration {integration.name!r} has no vendor"
                    )
                vendor = vendors_by_id.get(integration.vendor)
                if vendor is None:
                    raise ValueError(
                        f"integration {integration.name!r} references missing "
                        f"vendor {integration.vendor!r}"
                    )
                if (
                    integration.kind != "fake"
                    and integration.default_model
                    and not any(
                        model.id == integration.default_model
                        for model in vendor.models
                    )
                ):
                    raise ValueError(
                        f"integration {integration.name!r} default_model "
                        f"{integration.default_model!r} is not in the "
                        f"{vendor.id!r} model catalog"
                    )
        for key, binding in self.bindings.items():
            feature = FEATURE_BY_KEY.get(key)
            if feature is None:
                raise ValueError(f"unknown feature in bindings: {key}")
            if binding.integration_id is None:
                continue
            integration = by_id.get(binding.integration_id)
            if integration is None:
                raise ValueError(
                    f"feature {key} references missing integration {binding.integration_id}"
                )
            if integration.kind not in feature.kinds:
                raise ValueError(
                    f"feature {key} cannot use a {integration.kind} integration"
                )
            if feature.requires_model:
                model = binding.model or integration.default_model
                if integration.kind != "fake" and not model:
                    raise ValueError(f"feature {key} needs a model")
            if integration.kind == "openai" and not integration.api_key_env:
                raise ValueError(
                    f"feature {key} is bound to {integration.name!r}, which has "
                    "no api_key_env; set one or choose an internal provider"
                )
            if self.version >= 3 and binding.model:
                vendor = vendors_by_id[integration.vendor]
                if not any(model.id == binding.model for model in vendor.models):
                    raise ValueError(
                        f"feature {key} model {binding.model!r} is not in the "
                        f"{vendor.id!r} model catalog"
                    )
        return self

    def integration(self, integration_id: str) -> IntegrationRecord | None:
        for integration in self.integrations:
            if integration.id == integration_id:
                return integration
        return None

    def vendor(self, vendor_id: str) -> VendorRecord | None:
        for vendor in self.vendors:
            if vendor.id == vendor_id:
                return vendor
        return None


@dataclass(frozen=True)
class ResolvedProvider:
    """Everything a dynamic adapter needs to build one real provider."""

    feature: str
    source: Literal["admin", "config"]
    integration_id: str | None
    integration_name: str
    vendor: str
    vendor_label: str
    kind: IntegrationKind
    base_url: str
    api_key_env: str
    model: str
    options: dict[str, Any]
