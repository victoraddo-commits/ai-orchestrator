"""§37 MODULE INTEGRATION — API surface.

Lets a module (or the Command Center on its behalf) query the module capability
catalog and request a capability from KAI's unified workforce. Auth reuses the
same operator gate as the teammate/workforce API (bridge token or
``delegate.use`` session), so no new credential path is introduced.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.teammate.routes import _require_operator
from core.integration.module_bridge import get_bridge

logger = logging.getLogger(__name__)

integration_router = APIRouter(tags=["module-integration"])


class CapabilityRequestBody(BaseModel):
    objective: Optional[str] = None
    execute: bool = True
    background: bool = False
    project_path: Optional[str] = None


@integration_router.get("/api/integration/modules")
def list_module_integrations(operator: str = Depends(_require_operator)):
    """Every module and the capabilities it can request from KAI."""
    return {"operator": operator, "modules": get_bridge().list_module_capabilities()}


@integration_router.post(
    "/api/integration/modules/{module}/capabilities/{capability}/request")
def request_module_capability(module: str, capability: str,
                              body: CapabilityRequestBody = CapabilityRequestBody(),
                              operator: str = Depends(_require_operator)):
    """A module asks KAI for a capability -> teammate + mission."""
    try:
        result = get_bridge().request_capability(
            module, capability, objective=body.objective, execute=body.execute,
            background=body.background, project_path=body.project_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("module capability request failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    return {"operator": operator, **result}


@integration_router.get("/api/integration/modules/{module}/requests")
def list_module_requests(module: str, operator: str = Depends(_require_operator)):
    """The module's capability-request journal (mission + outcome)."""
    return {"operator": operator, "requests": get_bridge().list_requests(module)}
