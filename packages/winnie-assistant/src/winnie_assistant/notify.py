"""The boundary between the assistant and push notifications.

The assistant must not import ``winnie-push``. It declares one operation —
"send the owner a notice" — and the host implements it over the push service.
With no implementation the notify skill is reported unavailable.
"""

from __future__ import annotations

from typing import Any, Protocol


class Notifier(Protocol):
    async def notify(
        self,
        *,
        title: str,
        body: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
