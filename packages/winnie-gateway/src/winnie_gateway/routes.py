"""A gateway route: one path prefix and the upstream it forwards to.

Routes no longer carry a protocol-specific set of public paths. Every request
under ``/{route}/`` is forwarded as-is, so the only thing a route needs is an
upstream base URL and the credential the server injects for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Protocol = Literal["openai", "anthropic"]


@dataclass(frozen=True)
class Route:
    name: str
    protocol: Protocol
    base_url: str
    # Which supplier credential to inject. The key itself is held by the host
    # and read from the supplier record in the AI registry; ``repr=False``
    # keeps it out of tracebacks and log lines that format a route.
    vendor: str = ""
    api_key: str = field(default="", repr=False)
    # The env variable the console mirrors this supplier key into. It names the
    # backup copy, never the source.
    api_key_env: str = ""

    def api_base(self) -> str:
        """The upstream API root, normalised to end with ``/v1``."""
        base = self.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return base

    def upstream_url(self, path: str) -> str:
        """Join a client path onto the configured base URL.

        A client always calls ``/{route}/v1/...``. A base that already ends in
        ``/v1`` (a common way to configure an OpenAI-compatible endpoint) must
        not produce ``/v1/v1/...``, so it is trimmed before the join.
        """
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3].rstrip("/")
        return f"{base}/{path.lstrip('/')}"

    @property
    def credential_present(self) -> bool:
        return bool(self.api_key)

    def view(self) -> dict[str, object]:
        """The console-facing shape: everything except the credential."""
        return {
            "name": self.name,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "api_base": self.api_base(),
            "path_prefix": f"/{self.name}/",
            "vendor": self.vendor,
            "api_key_env": self.api_key_env,
            "credential_present": self.credential_present,
        }
