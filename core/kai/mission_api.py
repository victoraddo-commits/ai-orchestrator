"""
Mission Control FastAPI routes.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List

from core.kai.mission_engine import (
    create_mission,
    approve_mission,
    execute_mission,
    checkpoint_mission,
)
from core.kai.mission_store import get_mission, load_missions
from core.kai.mission_dashboard import get_mission_summary
from core.kai.mission_control import (
    pause_mission,
    resume_mission,
    stop_mission,
    redirect_mission,
    replan_mission,
)

router = APIRouter(prefix="/kai/missions", tags=["kai-missions"])


class CreateMissionRequest(BaseModel):
    objective: str
    models: List[str] = []
    tools: List[str] = []
    constraints: List[str] = []
    success_criteria: List[str] = []
    risk: str = "low"
    budget_usd: Optional[float] = None
    deadline: Optional[str] = None


class SteeringRequest(BaseModel):
    action: str
    note: Optional[str] = None
    new_objective: Optional[str] = None
    new_plan: Optional[List[str]] = None


@router.post("")
def api_create_mission(body: CreateMissionRequest):
    ctx = {"user_id": "api", "channel": "api"}
    m = create_mission(
        objective=body.objective,
        context=ctx,
        models=body.models,
        tools=body.tools,
        constraints=body.constraints,
        success_criteria=body.success_criteria,
        risk=body.risk,
        budget=body.budget_usd,
        deadline=body.deadline,
    )
    return {"id": m["id"], "status": m["status"]}


@router.get("")
def api_list_missions(status: Optional[str] = None, limit: int = 50):
    return load_missions(status=status)[:limit]


@router.get("/summary")
def api_mission_summary():
    return get_mission_summary()


@router.get("/{mission_id}")
def api_get_mission(mission_id: str):
    m = get_mission(mission_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return m


@router.post("/{mission_id}/approve")
def api_approve_mission(mission_id: str):
    m = approve_mission(mission_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return {"id": mission_id, "status": m.get("status")}


@router.post("/{mission_id}/execute")
def api_execute_mission(mission_id: str, background_tasks: BackgroundTasks):
    m = get_mission(mission_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    background_tasks.add_task(execute_mission, mission_id)
    return {"id": mission_id, "status": "execution_started"}


@router.post("/{mission_id}/steer")
def api_steering(mission_id: str, body: SteeringRequest):
    try:
        action = body.action
        if action == "pause":
            m = pause_mission(mission_id, note=body.note)
        elif action == "resume":
            m = resume_mission(mission_id, note=body.note)
        elif action == "stop":
            m = stop_mission(mission_id, note=body.note)
        elif action == "redirect":
            if not body.new_objective:
                raise ValueError("new_objective required for redirect")
            m = redirect_mission(mission_id, body.new_objective, note=body.note)
        elif action == "replan":
            if not body.new_plan:
                raise ValueError("new_plan required for replan")
            m = replan_mission(mission_id, body.new_plan, note=body.note)
        else:
            raise ValueError(f"Unknown steering action: {action}")
        return m
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{mission_id}/checkpoint")
def api_get_checkpoint(mission_id: str):
    m = get_mission(mission_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return m.get("checkpoint", {})


@router.post("/{mission_id}/checkpoint")
def api_manual_checkpoint(mission_id: str):
    try:
        cp = checkpoint_mission(
            mission_id, completed_action=None, artifacts=[], worker_states={}
        )
        return {"status": "checkpointed", "checkpoint": cp}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
