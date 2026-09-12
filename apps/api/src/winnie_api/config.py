"""运行时配置。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_API_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _API_ROOT.parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_API_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI 兼容上游
    upstream_api_key: str = Field(default="", alias="UPSTREAM_API_KEY")
    upstream_base_url: str = Field(
        default="https://api.openai.com/v1", alias="UPSTREAM_BASE_URL"
    )
    upstream_default_model: str = Field(
        default="gpt-4o-mini", alias="UPSTREAM_DEFAULT_MODEL"
    )
    winnie_log_dir: str = Field(
        default=str(_REPO_ROOT / "data" / "logs" / "openai-proxy"),
        alias="WINNIE_LOG_DIR",
    )

    # Anthropic 原生上游
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com", alias="ANTHROPIC_BASE_URL"
    )
    anthropic_version: str = Field(
        default="2023-06-01", alias="ANTHROPIC_VERSION"
    )
    anthropic_default_model: str = Field(
        default="claude-sonnet-4-5", alias="ANTHROPIC_DEFAULT_MODEL"
    )
    anthropic_log_dir: str = Field(
        default=str(_REPO_ROOT / "data" / "logs" / "anthropic-proxy"),
        alias="ANTHROPIC_LOG_DIR",
    )

    winnie_api_key: str = Field(default="", alias="WINNIE_API_KEY")

    def normalized_upstream_base(self) -> str:
        base = self.upstream_base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return base

    def normalized_anthropic_base(self) -> str:
        """Anthropic 官方根不含 /v1；统一成 …/v1。"""
        base = self.anthropic_base_url.rstrip("/")
        if base.endswith("/v1"):
            return base
        return f"{base}/v1"

    def resolve_log_dir(self) -> Path:
        return self._resolve_path(self.winnie_log_dir)

    def resolve_anthropic_log_dir(self) -> Path:
        return self._resolve_path(self.anthropic_log_dir)

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = (_API_ROOT / path).resolve()
        return path


def get_settings() -> Settings:
    return Settings()
