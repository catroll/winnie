"""Client for the local document index (``winnie-index``).

The index owns tokenisation, chunking, ranking and freshness. The assistant
sends a query and reads documents back, so there is exactly one retrieval
implementation in the project. Search hits carry root-relative paths, headings
and line ranges, which is what makes an answer citable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:8790"


class IndexUnavailable(RuntimeError):
    """The index daemon could not answer the request."""


@dataclass(frozen=True)
class IndexHit:
    path: str
    root: str
    heading: str
    start_line: int
    end_line: int
    snippet: str
    score: float
    mtime: str
    also_at: tuple[str, ...] = ()
    # The daemon widened the query to an OR of its tokens because the exact
    # phrasing matched nothing, so the snippet is related wording, not the
    # wording asked for.
    loose: bool = False


class IndexClient:
    def __init__(
        self,
        *,
        url: str = DEFAULT_URL,
        http: httpx.AsyncClient,
        token_env: str = "WINNIE_INDEX_TOKEN",
        timeout_seconds: float = 5.0,
        limit: int = 8,
    ) -> None:
        self.url = url.rstrip("/") or DEFAULT_URL
        self.http = http
        self.token_env = token_env
        self.timeout_seconds = timeout_seconds
        self.limit = limit

    # -- reads -----------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        roots: list[str] | None = None,
        since: str | None = None,
        loose: bool = True,
    ) -> list[IndexHit]:
        """Search the index.

        ``loose`` is on by default here, unlike on the daemon's own API: the
        assistant turns "nothing found" into a statement to the owner, and a
        paraphrase of a memory note must not read as "there is no such note".
        Hits carry ``loose`` so the tool can say the wording was a fallback.
        """
        payload: dict[str, Any] = {
            "query": query,
            "limit": max(1, min(limit or self.limit, 50)),
        }
        if roots:
            payload["roots"] = roots
        if since:
            payload["since"] = since
        if loose:
            payload["loose"] = True
        data = await self._request("POST", "/search", json=payload)
        results = data.get("results")
        if not isinstance(results, list):
            raise IndexUnavailable("the index returned no results list")
        hits: list[IndexHit] = []
        for item in results:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            hits.append(
                IndexHit(
                    path=str(item["path"]),
                    root=str(item.get("root") or ""),
                    heading=str(item.get("heading") or ""),
                    start_line=int(item.get("start_line") or 0),
                    end_line=int(item.get("end_line") or 0),
                    snippet=str(item.get("snippet") or ""),
                    score=float(item.get("score") or 0.0),
                    mtime=str(item.get("mtime") or ""),
                    also_at=tuple(str(path) for path in item.get("also_at") or []),
                    loose=bool(item.get("loose")),
                )
            )
        return hits

    async def read(
        self,
        path: str,
        *,
        from_line: int = 1,
        lines: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"path": path, "from": max(1, from_line)}
        if lines:
            params["lines"] = max(1, min(lines, 2000))
        return await self._request("GET", "/doc", params=params)

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/status")

    async def reindex(self, path: str | Path) -> dict[str, Any]:
        """Index one file now, instead of waiting for the daemon's next sweep.

        The sweep runs every ``scan.interval_seconds`` (five minutes by
        default), so a memory note written by ``remember`` would otherwise be
        unfindable for up to that long — which is exactly how the owner's rule
        got lost on 2026-11-01. The daemon refuses a path outside its roots, an
        unindexed file type and an oversized file, and those refusals surface as
        ``IndexUnavailable`` like any other daemon error.
        """
        return await self._request(
            "POST", "/admin/index", json={"path": str(path)}
        )

    # -- transport -------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        token = os.environ.get(self.token_env, "") if self.token_env else ""
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self.http.request(
                method,
                f"{self.url}{path}",
                json=json,
                params=params,
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise IndexUnavailable(
                f"the local index at {self.url} is unreachable ({type(exc).__name__})"
            ) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise IndexUnavailable(
                f"the local index returned a non-JSON body (HTTP {response.status_code})"
            ) from exc
        if response.status_code >= 400:
            detail = data.get("error") if isinstance(data, dict) else None
            raise IndexUnavailable(
                f"the local index returned HTTP {response.status_code}"
                + (f": {detail}" if detail else "")
            )
        if not isinstance(data, dict):
            raise IndexUnavailable("the local index returned an unexpected payload")
        return data
