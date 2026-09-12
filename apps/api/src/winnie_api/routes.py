"""按路径前缀区分上游：/{route}/v1/...

密钥不由服务端保管，一律从客户端请求头透传。
路由表来自 config.yaml。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Protocol = Literal["openai", "anthropic"]


@dataclass(frozen=True)
class Route:
    name: str
    protocol: Protocol
    base_url: str

    def api_base(self) -> str:
        """归一化为以 /v1 结尾的上游 API 根。"""
        base = self.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return base
