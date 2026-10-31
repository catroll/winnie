"""The boundary between the assistant and the news capability.

The assistant package must not import ``winnie-news``: capabilities do not know
each other. The assistant declares what its news tools need, and the host
implements this protocol over the news service. With no implementation the news
skill is reported unavailable instead of being offered and failing.
"""

from __future__ import annotations

from typing import Any, Protocol


class NewsGateway(Protocol):
    """What the assistant's news tools may ask for."""

    async def status(self) -> dict[str, Any]: ...

    async def start(self) -> dict[str, Any]: ...

    async def run(self, run_id: str = "") -> dict[str, Any]: ...

    async def drafts(self, run_id: str = "") -> list[dict[str, Any]]: ...

    async def draft(self, run_id: str, event_id: str) -> str: ...
