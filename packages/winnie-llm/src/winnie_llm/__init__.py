"""Winnie's single model-access module.

Every call to a model vendor — chat completion, tool calling, recording-file
speech recognition and live captions — is built here. Each supplier's
credential is stored with its model catalog in the registry, and capability
packages receive a client or a small protocol; they never read a key, name a key
variable, or issue a vendor HTTP request themselves. The console mirrors every
key into ``packages/winnie-llm/.env`` as a backup, which is also the fallback for
a gateway-only deployment that has no registry.

The :class:`winnie_llm.registry.ProviderRegistry` owns the supplier/model
selection that used to live in the API host: it persists ``data/ai.yaml``,
resolves a feature to a supplier model, and builds the matching client.
"""

from winnie_llm.asr import (
    ASRError,
    ASRNoSpeech,
    ASRProvider,
    ASRResult,
    ASRSegment,
    FakeASRProvider,
    FakeRealtimeASR,
    RealtimeASRError,
    RealtimeASRProvider,
    RealtimeEvent,
    StreamStarted,
    TranscriptUpdate,
    VolcengineASRConfig,
    VolcengineASRProvider,
    VolcengineRealtimeASR,
)
from winnie_llm.chat import (
    ChatClient,
    ChatResult,
    LLMCompletion,
    LLMInsufficientBalance,
    StreamPart,
    ToolCall,
)
from winnie_llm.json import parse_json_value
from winnie_llm.keys import (
    DEFAULT_ENV_PATH,
    VENDOR_KEY_ENV,
    ApiKeys,
    MissingApiKey,
    env_name_for,
    load_keys,
)
from winnie_llm.registry import (
    AIConflict,
    AINotFound,
    AIProviderError,
    AIState,
    BindingDraft,
    BindingRecord,
    FEATURE_BY_KEY,
    FEATURES,
    IntegrationDraft,
    IntegrationKind,
    IntegrationRecord,
    KINDS,
    ModelRecord,
    ProviderRegistry,
    ResolvedProvider,
    RouteEntry,
    SeedInput,
    SeedRoute,
    VendorCheck,
    VendorDraft,
    VendorRecord,
    VirtualModelDraft,
    VirtualModelRecord,
    infer_vendor,
    vendor_label_for,
)
from winnie_llm.runtime import ModelRuntimeConfig

__all__ = [
    "AIProviderError",
    "AIConflict",
    "AINotFound",
    "AIState",
    "ASRError",
    "ASRNoSpeech",
    "ASRProvider",
    "ASRResult",
    "ASRSegment",
    "ApiKeys",
    "BindingDraft",
    "BindingRecord",
    "ChatClient",
    "ChatResult",
    "DEFAULT_ENV_PATH",
    "FEATURES",
    "FEATURE_BY_KEY",
    "FakeASRProvider",
    "FakeRealtimeASR",
    "IntegrationDraft",
    "IntegrationKind",
    "IntegrationRecord",
    "KINDS",
    "LLMCompletion",
    "LLMInsufficientBalance",
    "MissingApiKey",
    "ModelRecord",
    "ModelRuntimeConfig",
    "ProviderRegistry",
    "RealtimeASRError",
    "RealtimeASRProvider",
    "RealtimeEvent",
    "ResolvedProvider",
    "RouteEntry",
    "SeedInput",
    "SeedRoute",
    "StreamPart",
    "StreamStarted",
    "ToolCall",
    "TranscriptUpdate",
    "VENDOR_KEY_ENV",
    "VendorCheck",
    "VendorDraft",
    "VendorRecord",
    "VirtualModelDraft",
    "VirtualModelRecord",
    "VolcengineASRConfig",
    "VolcengineASRProvider",
    "VolcengineRealtimeASR",
    "env_name_for",
    "infer_vendor",
    "load_keys",
    "parse_json_value",
    "vendor_label_for",
]
