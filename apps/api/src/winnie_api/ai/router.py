"""Console API for the admin-managed AI provider registry.

Mounted at ``/api/v1/ai`` when ``ai.enabled`` is true. Every mutation refreshes
the in-process gateway route table, so a provider added in the console is
reachable at ``/{name}/v1/...`` without a restart.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from winnie_core.auth.dependencies import get_user_id
from winnie_core.config import ModelRuntimeConfig

from winnie_api.ai.errors import AIConflict, AINotFound, AIProviderError
from winnie_api.ai.schema import BindingDraft, IntegrationDraft, VendorDraft
from winnie_api.ai.store import AISettingsService

router = APIRouter(prefix="/ai", tags=["ai"])


def _service(request: Request) -> AISettingsService:
    service = getattr(request.app.state, "ai_settings", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="AI provider management is disabled (ai.enabled is false)",
        )
    return service


def _error(exc: AIProviderError) -> HTTPException:
    if isinstance(exc, AINotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, AIConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _refresh_routes(request: Request, service: AISettingsService) -> None:
    if hasattr(request.app.state, "routes_table"):
        request.app.state.routes_table = service.route_table()


def _integration_view(
    service: AISettingsService, integration_id: str
) -> dict[str, Any]:
    for integration in service.view()["integrations"]:
        if integration["id"] == integration_id:
            return integration
    raise HTTPException(status_code=404, detail="integration not found")


def _vendor_view(service: AISettingsService, vendor_id: str) -> dict[str, Any]:
    for vendor in service.view()["vendors"]:
        if vendor["id"] == vendor_id:
            return vendor
    raise HTTPException(status_code=404, detail="supplier not found")


@router.get("/config")
async def get_config(
    request: Request,
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    """Suppliers, endpoints, models, feature bindings and runtime limits."""
    return _service(request).view()


@router.post("/vendors", status_code=201)
async def create_vendor(
    request: Request,
    payload: VendorDraft,
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    service = _service(request)
    try:
        vendor = service.create_vendor(payload)
    except AIProviderError as exc:
        raise _error(exc) from exc
    return {"vendor": _vendor_view(service, vendor.id)}


@router.post("/vendors/{vendor_id}/models/sync")
async def sync_vendor_models(
    request: Request,
    vendor_id: str,
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    service = _service(request)
    try:
        vendor = await service.sync_models(
            vendor_id, getattr(request.app.state, "http", None)
        )
    except AIProviderError as exc:
        raise _error(exc) from exc
    return {"vendor": _vendor_view(service, vendor.id)}


@router.put("/vendors/{vendor_id}")
async def update_vendor(
    request: Request,
    vendor_id: str,
    payload: VendorDraft,
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    service = _service(request)
    try:
        vendor = service.update_vendor(vendor_id, payload)
    except AIProviderError as exc:
        raise _error(exc) from exc
    return {"vendor": _vendor_view(service, vendor.id)}


@router.post("/integrations", status_code=201)
async def create_integration(
    request: Request,
    payload: IntegrationDraft,
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    service = _service(request)
    try:
        record = service.create_integration(payload)
    except AIProviderError as exc:
        raise _error(exc) from exc
    _refresh_routes(request, service)
    return {"integration": _integration_view(service, record.id)}


@router.put("/integrations/{integration_id}")
async def update_integration(
    request: Request,
    integration_id: str,
    payload: IntegrationDraft,
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    service = _service(request)
    try:
        record = service.update_integration(integration_id, payload)
    except AIProviderError as exc:
        raise _error(exc) from exc
    _refresh_routes(request, service)
    return {"integration": _integration_view(service, record.id)}


@router.delete("/integrations/{integration_id}")
async def delete_integration(
    request: Request,
    integration_id: str,
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    service = _service(request)
    try:
        name, cleared = service.delete_integration(integration_id)
    except AIProviderError as exc:
        raise _error(exc) from exc
    _refresh_routes(request, service)
    return {"name": name, "cleared_features": cleared}


@router.put("/runtime")
async def set_runtime(
    request: Request,
    payload: ModelRuntimeConfig,
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    runtime = _service(request).set_runtime(payload)
    return {"runtime": runtime.model_dump(mode="json")}


@router.put("/features/{feature}")
async def set_binding(
    request: Request,
    feature: str,
    payload: BindingDraft,
    _: str = Depends(get_user_id),
) -> dict[str, Any]:
    service = _service(request)
    try:
        binding = service.set_binding(feature, payload)
    except AIProviderError as exc:
        raise _error(exc) from exc
    return {"feature": feature, "binding": binding.model_dump(mode="json")}
