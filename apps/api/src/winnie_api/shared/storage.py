"""Audio storage interface and local-disk implementation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    async def save(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        max_bytes: int,
    ) -> int: ...

    async def read(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    def path_for(self, key: str) -> Path | None: ...


class LocalStorage:
    """Store audio under a root directory. Suitable for the first MVP."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("storage key escapes the configured root")
        return path

    async def save(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        max_bytes: int,
    ) -> int:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        handle = await asyncio.to_thread(path.open, "wb")
        total = 0
        try:
            async for chunk in chunks:
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"upload exceeds the {max_bytes} byte limit")
                await asyncio.to_thread(handle.write, chunk)
        except Exception:
            await asyncio.to_thread(handle.close)
            await asyncio.to_thread(path.unlink, missing_ok=True)
            raise
        await asyncio.to_thread(handle.close)
        return total

    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    def path_for(self, key: str) -> Path | None:
        return self._path(key)
