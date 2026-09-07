"""ModelFactory：多 Provider → 统一 ChatModel 接口。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

Provider = Literal["openai", "deepseek", "qwen", "claude"]

PROVIDERS: tuple[Provider, ...] = ("openai", "deepseek", "qwen", "claude")


@dataclass(frozen=True)
class ProviderConfig:
    name: Provider
    api_key_env: str
    base_url_env: str
    model_env: str
    default_model: str
    default_base_url: str | None = None


# 全部走 OpenAI 兼容协议 + init_chat_model("openai:...")
# → 业务只依赖 BaseChatModel，不绑死某一家 SDK 调用细节。
_REGISTRY: dict[Provider, ProviderConfig] = {
    "openai": ProviderConfig(
        name="openai",
        api_key_env="OPENAI_API_KEY",
        base_url_env="OPENAI_BASE_URL",
        model_env="OPENAI_MODEL",
        default_model="gpt-4o-mini",
        default_base_url="https://api.openai.com/v1",
    ),
    "deepseek": ProviderConfig(
        name="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
        model_env="DEEPSEEK_MODEL",
        default_model="deepseek-chat",
        default_base_url="https://api.deepseek.com/v1",
    ),
    "qwen": ProviderConfig(
        name="qwen",
        api_key_env="QWEN_API_KEY",
        base_url_env="QWEN_BASE_URL",
        model_env="QWEN_MODEL",
        default_model="qwen-plus",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "claude": ProviderConfig(
        name="claude",
        api_key_env="CLAUDE_API_KEY",
        base_url_env="CLAUDE_BASE_URL",
        model_env="CLAUDE_MODEL",
        default_model="claude-3-5-sonnet-latest",
        default_base_url=None,  # 需自备兼容网关或填官方代理 BASE_URL
    ),
}


class ModelFactory:
    """统一入口：create(provider) → BaseChatModel。"""

    def __init__(self, *, temperature: float = 0) -> None:
        self.temperature = temperature

    def available(self) -> list[Provider]:
        return [p for p in PROVIDERS if self._api_key(p)]

    def create(self, provider: Provider | str | None = None) -> BaseChatModel:
        name = self._resolve_provider(provider)
        cfg = _REGISTRY[name]
        api_key = self._api_key(name)
        if not api_key:
            raise ValueError(
                f"provider={name!r} 缺少密钥环境变量 {cfg.api_key_env}。"
                f" 已配置: {self.available() or '无'}"
            )
        base_url = os.getenv(cfg.base_url_env) or cfg.default_base_url
        model = os.getenv(cfg.model_env) or cfg.default_model
        kwargs: dict = {"temperature": self.temperature, "api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        # ChatOpenAI 路径的统一抽象（兼容网关上的 DeepSeek / Qwen / Claude）
        return init_chat_model(f"openai:{model}", **kwargs)

    def describe(self, provider: Provider | str | None = None) -> dict[str, str | None]:
        name = self._resolve_provider(provider)
        cfg = _REGISTRY[name]
        return {
            "provider": name,
            "model": os.getenv(cfg.model_env) or cfg.default_model,
            "base_url": os.getenv(cfg.base_url_env) or cfg.default_base_url,
            "api_key_env": cfg.api_key_env,
            "configured": bool(self._api_key(name)),
        }

    def _resolve_provider(self, provider: Provider | str | None) -> Provider:
        if provider:
            p = provider.strip().lower()
            if p not in _REGISTRY:
                raise ValueError(f"未知 provider={provider!r}，可选: {list(PROVIDERS)}")
            return p  # type: ignore[return-value]
        default = (os.getenv("DEFAULT_PROVIDER") or "openai").strip().lower()
        if default in _REGISTRY and self._api_key(default):  # type: ignore[arg-type]
            return default  # type: ignore[return-value]
        available = self.available()
        if not available:
            raise ValueError("没有任何 provider 配置了 API Key。请编辑 .env。")
        return available[0]

    @staticmethod
    def _api_key(provider: Provider) -> str | None:
        cfg = _REGISTRY[provider]
        # 兼容：若未单独配 DEEPSEEK_API_KEY 等，可回退 OPENAI_API_KEY（同一网关多模型）
        key = os.getenv(cfg.api_key_env) or os.getenv("OPENAI_API_KEY")
        return key or None
