"""Console API for collected errors, their triage state and their diagnosis.

Administrator-only: a traceback names internal paths, configuration and data
shapes, which is exactly what must not reach a non-admin account. The store is
the only writer; this module translates HTTP into its calls and its domain
errors back into statuses the console can act on.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from winnie_core.auth.dependencies import admin_required

from .service import ChatFn, ErrorService, IncidentNotFound, serialize

router = APIRouter(
    prefix="/errors",
    tags=["errors"],
    dependencies=[Depends(admin_required)],
)


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


def get_service(request: Request) -> ErrorService:
    service = getattr(request.app.state, "devloop_service", None)
    if service is None:
        raise HTTPException(
            status_code=503, detail="error collection is disabled (devloop.enabled is false)"
        )
    return service


@router.get("/summary")
async def summary(request: Request) -> dict[str, Any]:
    return await get_service(request).summary()


@router.get("")
async def list_incidents(
    request: Request,
    status: str = Query("open", description="open | acknowledged | ignored | resolved | all"),
    service: str = Query(""),
    source: str = Query(""),
    query: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    rows, total = await get_service(request).list_incidents(
        status=status,
        service=service,
        source=source,
        query=query,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [serialize(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{incident_id}")
async def get_incident(request: Request, incident_id: UUID) -> dict[str, Any]:
    try:
        return serialize(await get_service(request).get(incident_id))
    except IncidentNotFound as exc:
        raise HTTPException(status_code=404, detail="no such incident") from exc


@router.post("/{incident_id}/status")
async def set_status(
    request: Request, incident_id: UUID, payload: StatusUpdate
) -> dict[str, Any]:
    try:
        incident = await get_service(request).set_status(incident_id, payload.status)
    except IncidentNotFound as exc:
        raise HTTPException(status_code=404, detail="no such incident") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return serialize(incident)


@router.post("/{incident_id}/diagnose")
async def diagnose(request: Request, incident_id: UUID) -> dict[str, Any]:
    """Explain one incident from local evidence. Read-only by construction.

    The model is asked to answer with a structured diagnosis; nothing it says is
    executed. A missing model binding is a 503, not a silent empty answer.
    """
    service = get_service(request)
    diagnoser = getattr(request.app.state, "devloop_diagnoser", None)
    sources = getattr(request.app.state, "devloop_sources", None)
    if diagnoser is None or sources is None:
        raise HTTPException(
            status_code=503,
            detail="diagnosis is unavailable: no chat model is bound",
        )
    chat: ChatFn
    chat, model_name = diagnoser
    config = request.app.state.devloop_config
    try:
        incident = await service.diagnose(
            incident_id,
            chat=chat,
            model_name=model_name,
            sources=sources,
            max_evidence_bytes=config.diagnose.max_evidence_bytes,
            timeout_seconds=config.diagnose.timeout_seconds,
        )
    except IncidentNotFound as exc:
        raise HTTPException(status_code=404, detail="no such incident") from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - a provider failure is a 502
        raise HTTPException(
            status_code=502, detail=f"the diagnosis model failed: {type(exc).__name__}: {exc}"
        ) from exc
    return serialize(incident)

