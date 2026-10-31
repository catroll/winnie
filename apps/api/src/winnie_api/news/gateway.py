"""Expose the news service to the assistant through its declared protocol."""

from __future__ import annotations

from typing import Any

from winnie_news.service import NewsService


class NewsGatewayAdapter:
    """Adapt ``NewsService`` to ``winnie_assistant.news.NewsGateway``.

    The assistant never imports the news package; this adapter is the only place
    where the two meet, and the run-id defaults ("latest run") live here because
    the assistant's tools accept an optional id.
    """

    def __init__(self, service: NewsService) -> None:
        self.service = service

    async def status(self) -> dict[str, Any]:
        return self.service.status()

    async def start(self) -> dict[str, Any]:
        run = await self.service.start_run()
        return {"run_id": run.id, "status": run.status}

    async def run(self, run_id: str = "") -> dict[str, Any]:
        record = self._resolve(run_id)
        return record.model_dump()

    async def drafts(self, run_id: str = "") -> list[dict[str, Any]]:
        record = self._resolve(run_id)
        reviews = {
            review.get("event_id"): review
            for review in self.service.run_reviews(record.id)
        }
        drafts = self.service.run_drafts(record.id)
        for entry in drafts:
            review = reviews.get(entry.get("event_id")) or {}
            entry["review_status"] = review.get("status", "")
        return drafts

    async def draft(self, run_id: str, event_id: str) -> str:
        record = self._resolve(run_id)
        return self.service.draft_markdown(record.id, event_id)

    def _resolve(self, run_id: str):
        if run_id:
            return self.service.get_run(run_id)
        runs = self.service.list_runs(limit=1)
        if not runs:
            raise LookupError("no news run has been started yet")
        return runs[0]
