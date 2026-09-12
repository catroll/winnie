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

    upstream_api_key: str = Field(default="", alias="UPSTREAM_API_KEY")
    upstream_base_url: str = Field(
        default="https://api.openai.com/v1", alias="UPSTREAM_BASE_URL"
    )
    upstream_default_model: str = Field(
        default="gpt-4o-mini", alias="UPSTREAM_DEFAULT_MODEL"
    )

    winnie_api_key: str = Field(default="", alias="WINNIE_API_KEY")
    winnie_log_dir: str = Field(
        default=str(_REPO_ROOT / "data" / "logs" / "openai-proxy"),
        alias="WINNIE_LOG_DIR",
    )

    def normalized_upstream_base(self) -> str:
        base = self.upstream_base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return base

    def resolve_log_dir(self) -> Path:
        path = Path(self.winnie_log_dir)
        if not path.is_absolute():
            path = (_API_ROOT / path).resolve()
        return path


def get_settings() -> Settings:
    return Settings()
