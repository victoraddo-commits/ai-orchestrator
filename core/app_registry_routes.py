"""FastAPI router for the Application Registry (Phase 19R)."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.app_registry import (
    create_entry as _create_entry,
    get_entry as _get_entry,
    list_entries as _list_entries,
    update_entry as _update_entry,
    delete_entry as _delete_entry,
    health_check as _health_check,
    DuplicateAppName,
    AppNotFound,
    AppRegistryError,
)
from core.app_registry_models import (
    AppCreate,
    AppUpdate,
    AppRecord,
    ChangeRecord,
    RegistryStatus,
)

router = APIRouter(prefix="/api/v1/registry", tags=["registry"])


@router.post("/apps", response_model=AppRecord, status_code=201)
def create_app(app: AppCreate):
    try:
        return _create_entry(app)
    except DuplicateAppName as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/apps", response_model=list[AppRecord])
def list_apps(
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    metadata: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="created_at", pattern=r"^(created_at|updated_at)$"),
):
    metadata_filter = None
    if metadata:
        try:
            import json
            metadata_filter = json.loads(metadata)
        except json.JSONDecodeError:
            metadata_filter = {}
    return _list_entries(
        status=status,
        search=search,
        metadata_filter=metadata_filter,
        limit=limit,
        offset=offset,
        sort=sort,
    )


@router.get("/apps/{app_id}", response_model=AppRecord)
def get_app(app_id: str):
    record = _get_entry(app_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"app {app_id!r} not found")
    return record


@router.get("/apps/{app_id}/history", response_model=list[ChangeRecord])
def get_app_history(app_id: str):
    record = _get_entry(app_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"app {app_id!r} not found")
    return record.history


@router.patch("/apps/{app_id}", response_model=AppRecord)
def update_app(app_id: str, update: AppUpdate):
    try:
        record = _update_entry(app_id, update)
        return record
    except AppNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/apps/{app_id}", response_model=AppRecord)
def delete_app(app_id: str):
    try:
        record = _delete_entry(app_id)
        return record
    except AppNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/health")
def registry_health():
    healthy = _health_check()
    return {"status": "ok" if healthy else "degraded", "healthy": healthy}
