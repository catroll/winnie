"""HTTP API for the news capability, mounted under ``/api/v1``.

The console and the assistant both operate the same service object. Starting a
run returns immediately with a run id; the console polls the run for stage
progress, and a draft is readable as JSON or as the Markdown a person edits.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from winnie_core.auth.dependencies import admin_required
from winnie_news.service import NewsService, NewsUnavailable, RunBusy, RunNotFound
from winnie_news.store import RunRecord

router = APIRouter(
    prefix="/news",
    tags=["news"],
    dependencies=[Depends(admin_required)],
)


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Empty means "use the configured source types".
    source_types: list[str] = Field(default_factory=list)


def get_service(request: Request) -> NewsService:
    service = getattr(request.app.state, "news_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="news is disabled")
    return service


@router.get("/status")
async def status(
    service: NewsService = Depends(get_service),
) -> dict[str, Any]:
    return service.status()


@router.post("/runs", response_model=RunRecord, status_code=202)
async def start_run(
    payload: RunCreate | None = None,
    service: NewsService = Depends(get_service),
) -> RunRecord:
    source_types = list(payload.source_types) if payload else []
    try:
        return await service.start_run(source_types=source_types or None)
    except RunBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NewsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/runs", response_model=list[RunRecord])
async def list_runs(
    limit: int = Query(default=20, ge=1, le=200),
    service: NewsService = Depends(get_service),
) -> list[RunRecord]:
    return service.list_runs(limit=limit)


@router.get("/runs/{run_id}", response_model=RunRecord)
async def get_run(
    run_id: str,
    service: NewsService = Depends(get_service),
) -> RunRecord:
    try:
        return service.get_run(run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}/drafts")
async def list_drafts(
    run_id: str,
    service: NewsService = Depends(get_service),
) -> dict[str, Any]:
    try:
        service.get_run(run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "run_id": run_id,
        "drafts": service.run_drafts(run_id),
        "reviews": service.run_reviews(run_id),
    }


@router.get("/runs/{run_id}/drafts/{event_id}")
async def get_draft(
    run_id: str,
    event_id: str,
    service: NewsService = Depends(get_service),
) -> dict[str, Any]:
    try:
        draft, review = service.draft(run_id, event_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"draft": draft.as_document(), "review": review}


@router.post("/runs/{run_id}/drafts/{event_id}/regenerate")
async def regenerate_draft(
    run_id: str,
    event_id: str,
    service: NewsService = Depends(get_service),
) -> dict[str, Any]:
    try:
        draft, review = await service.regenerate_draft(run_id, event_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NewsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"draft": draft.as_document(), "review": review.as_document()}


@router.get(
    "/runs/{run_id}/drafts/{event_id}/markdown",
    response_class=PlainTextResponse,
)
async def get_draft_markdown(
    run_id: str,
    event_id: str,
    service: NewsService = Depends(get_service),
) -> PlainTextResponse:
    try:
        text = service.draft_markdown(run_id, event_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@router.get("/sources")
async def list_sources(
    service: NewsService = Depends(get_service),
) -> dict[str, Any]:
    return service.source_health()


@router.get("/events")
async def list_events(
    limit: int = Query(default=50, ge=1, le=500),
    status: str = Query(default=""),
    service: NewsService = Depends(get_service),
) -> dict[str, Any]:
    events = service.list_events(limit=limit, status=status)
    return {"events": [_event_summary(event) for event in events]}


@router.get("/events/{event_id}")
async def get_event(
    event_id: str,
    service: NewsService = Depends(get_service),
) -> dict[str, Any]:
    event = service.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event.model_dump()


def _event_summary(event) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "summary": event.summary,
        "event_type": event.event_type,
        "status": event.status,
        "first_seen": event.first_seen,
        "last_seen": event.last_seen,
        "coverage": event.coverage.model_dump(),
    }
