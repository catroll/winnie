"""Console API for the supplier registry.

Mounted at ``/api/v1/ai`` when ``ai.enabled`` is true. Every mutation refreshes
the in-process gateway route table, so a provider added in the console is
reachable at ``/{name}/v1/...`` without a restart. That includes a supplier key
saved here: it is stored on the supplier record, mirrored into the winnie-llm
env backup, and used by both internal calls and the gateway immediately. The
registry itself and every vendor client belong to ``winnie-llm``; this module is
only the HTTP surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from winnie_core.auth.dependencies import admin_required
from winnie_llm import (
    AIConflict,
    AINotFound,
    AIProviderError,
    BindingDraft,
    IntegrationDraft,
    ModelRuntimeConfig,
    ProviderRegistry,
    VendorDraft,
    VirtualModelDraft,
)

router = APIRouter(
    prefix="/ai",
    tags=["ai"],
    dependencies=[Depends(admin_required)],
)


class ApiKeyDraft(BaseModel):
    """A supplier key. Write-only: no endpoint returns it in full."""

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(default="", max_length=4096)


class BindingBatchDraft(BaseModel):
    """Several feature bindings saved as one transaction.

    Keyed by feature so the caller states exactly which rows it means; an
    omitted feature keeps its current binding.
    """

    model_config = ConfigDict(extra="forbid")

    bindings: dict[str, BindingDraft] = Field(min_length=1)


def _service(request: Request) -> ProviderRegistry:
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


def _refresh_routes(request: Request, service: ProviderRegistry) -> None:
    """Rebuild the live table, credentials included, after a registry change."""
    rebuild = getattr(request.app.state, "rebuild_routes", None)
    if rebuild is not None:
        request.app.state.routes_table = rebuild()


def _integration_view(
    service: ProviderRegistry, integration_id: str
) -> dict[str, Any]:
    for integration in service.view()["integrations"]:
        if integration["id"] == integration_id:
            return integration
    raise HTTPException(status_code=404, detail="integration not found")


def _vendor_view(service: ProviderRegistry, vendor_id: str) -> dict[str, Any]:
    for vendor in service.view()["vendors"]:
        if vendor["id"] == vendor_id:
            return vendor
    raise HTTPException(status_code=404, detail="supplier not found")


def _virtual_view(service: ProviderRegistry, virtual_id: str) -> dict[str, Any]:
    for virtual in service.view()["virtual_models"]:
        if virtual["id"] == virtual_id:
            return virtual
    raise HTTPException(status_code=404, detail="virtual model not found")


@router.get("/config")
async def get_config(
    request: Request,
) -> dict[str, Any]:
    """Suppliers, endpoints, models, feature bindings and runtime limits."""
    return _service(request).view()


@router.post("/vendors", status_code=201)
async def create_vendor(
    request: Request,
    payload: VendorDraft,
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
) -> dict[str, Any]:
    service = _service(request)
    try:
        vendor = service.update_vendor(vendor_id, payload)
    except AIProviderError as exc:
        raise _error(exc) from exc
    return {"vendor": _vendor_view(service, vendor.id)}


@router.put("/vendors/{vendor_id}/key")
async def set_vendor_key(
    request: Request,
    vendor_id: str,
    payload: ApiKeyDraft,
) -> dict[str, Any]:
    """Store the supplier key and refresh the gateway credential.

    An empty value clears the key. The response reports only whether a key is
    set and its last four characters.
    """
    service = _service(request)
    try:
        vendor = service.set_api_key(vendor_id, payload.api_key)
    except AIProviderError as exc:
        raise _error(exc) from exc
    _refresh_routes(request, service)
    return {"vendor": _vendor_view(service, vendor.id)}


@router.post("/vendors/{vendor_id}/check")
async def check_vendor(
    request: Request,
    vendor_id: str,
) -> dict[str, Any]:
    """Probe the supplier's own API with the stored key, without changing it.

    The probe is the supplier's ``GET /models``: no tokens are spent, and the
    answer separates "the key is rejected" from "the endpoint failed" and from
    "a bound model is gone". Read-only: nothing is written to the registry and
    the key never appears in the response.
    """
    service = _service(request)
    try:
        check = await service.check_vendor(
            vendor_id, getattr(request.app.state, "http", None)
        )
    except AIProviderError as exc:
        raise _error(exc) from exc
    return {
        "check": {
            "state": check.state,
            "status": check.status,
            "url": check.url,
            "models": check.models,
            "missing_models": list(check.missing_models),
            "detail": check.detail,
        }
    }


@router.post("/integrations", status_code=201)
async def create_integration(
    request: Request,
    payload: IntegrationDraft,
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
) -> dict[str, Any]:
    runtime = _service(request).set_runtime(payload)
    return {"runtime": runtime.model_dump(mode="json")}


@router.post("/virtual-models", status_code=201)
async def create_virtual_model(
    request: Request,
    payload: VirtualModelDraft,
) -> dict[str, Any]:
    """Create an alias for one supplier model."""
    service = _service(request)
    try:
        record = service.create_virtual_model(payload)
    except AIProviderError as exc:
        raise _error(exc) from exc
    return {"virtual_model": _virtual_view(service, record.id)}


@router.put("/virtual-models/{virtual_id}")
async def update_virtual_model(
    request: Request,
    virtual_id: str,
    payload: VirtualModelDraft,
) -> dict[str, Any]:
    """Retarget or relabel an alias; every feature bound to it follows."""
    service = _service(request)
    try:
        record = service.update_virtual_model(virtual_id, payload)
    except AIProviderError as exc:
        raise _error(exc) from exc
    return {"virtual_model": _virtual_view(service, record.id)}


@router.delete("/virtual-models/{virtual_id}")
async def delete_virtual_model(
    request: Request,
    virtual_id: str,
) -> dict[str, Any]:
    """Delete an alias that no feature uses."""
    service = _service(request)
    try:
        deleted, used_by = service.delete_virtual_model(virtual_id)
    except AIProviderError as exc:
        raise _error(exc) from exc
    return {"id": deleted, "used_by": used_by}


@router.put("/features")
async def set_bindings(
    request: Request,
    payload: BindingBatchDraft,
) -> dict[str, Any]:
    """Save several feature bindings in one transaction.

    The console sends every row the owner changed with one request, so a typo in
    one row cannot leave the table half-applied. An empty binding unbinds its
    feature.
    """
    service = _service(request)
    try:
        saved = service.set_bindings(payload.bindings)
    except AIProviderError as exc:
        raise _error(exc) from exc
    return {
        "saved": len(saved),
        "bindings": {
            feature: binding.model_dump(mode="json")
            for feature, binding in saved.items()
        },
        "features": service.view()["features"],
    }


@router.put("/features/{feature}")
async def set_binding(
    request: Request,
    feature: str,
    payload: BindingDraft,
) -> dict[str, Any]:
    service = _service(request)
    try:
        binding = service.set_binding(feature, payload)
    except AIProviderError as exc:
        raise _error(exc) from exc
    return {"feature": feature, "binding": binding.model_dump(mode="json")}
