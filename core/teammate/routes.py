"""Workforce / team / mission API for the Teammate Factory (KAI 2.0 Phase 1).

Mounts the live teammate runtime so BOTH Kai (Command Center / Telegram) and
OpenCode can create teammates, form teams and run missions:

    POST   /api/workforce/teammates        create/reuse a teammate
    GET    /api/workforce/teammates        list teammates
    GET    /api/workforce/teammates/{id}   teammate detail
    POST   /api/workforce/teams            form a team from a requirement
    GET    /api/workforce/teams            list teams
    GET    /api/workforce/teams/{id}       team detail
    POST   /api/missions                   decompose + assign + execute a goal
    GET    /api/missions                   list missions
    GET    /api/missions/{id}              mission task graph + status

Auth follows the existing bridge-token/session pattern (see
``core/bridge_auth.py``): send ``Authorization: Bearer <token>`` (the token in
``~/.ai-orchestrator/api_token`` on the API host) or a
``X-Kai-Session: <session>`` header with the ``delegate.use`` capability.
"""
from __future__ import annotations

import hmac
import logging
import threading
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from core import authz
from core.bridge_auth import BRIDGE_OPERATOR, _load_api_token
from core.teammate.engine import WorkforceEngine

logger = logging.getLogger(__name__)

teammate_router = APIRouter(tags=["workforce-teammate-factory"])

_ENGINE: Optional[WorkforceEngine] = None
_ENGINE_LOCK = threading.RLock()


def get_engine() -> WorkforceEngine:
    """Return the process-wide workforce engine (built on the runtime singleton)."""
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = WorkforceEngine()
    return _ENGINE


def _require_operator(authorization: str | None = Header(default=None),
                      x_kai_session: str | None = Header(default=None)) -> str:
    """Operator gate: bridge token (operator) or a session with delegate.use.

    Returns a safe role label — never the raw session token — so responses
    cannot echo credentials back to the caller.
    """
    expected = f"Bearer {_load_api_token()}"
    if authorization and hmac.compare_digest(authorization.encode(), expected.encode()):
        return BRIDGE_OPERATOR
    if x_kai_session and authz.check_capability(x_kai_session, "delegate.use"):
        return "operator"
    raise HTTPException(status_code=401, detail="Missing or invalid credentials")


class TeammateCreate(BaseModel):
    role: str
    skills: Optional[list[str]] = None
    model: Optional[str] = None
    name: Optional[str] = None


class TeamCreate(BaseModel):
    requirement: Optional[str] = None
    mission: Optional[str] = None


class MissionCreate(BaseModel):
    goal: str
    execute: bool = True
    background: bool = False
    project_path: Optional[str] = None
    team_id: Optional[str] = None
    # Explicit-skill missions (§31/§43): a caller/module can name the exact
    # skills + specialization; the engine creates/reuses the capable teammate
    # and journals the capability gap it resolved.
    skills: Optional[list[str]] = None
    specialization: Optional[str] = None


# ── teammates ───────────────────────────────────────────────────────────────
@teammate_router.post("/api/workforce/teammates")
def create_teammate(body: TeammateCreate, operator: str = Depends(_require_operator)):
    """Create or reuse a teammate for ``role`` → returns id + READY status."""
    try:
        mate = get_engine().create_teammate(
            body.role, skills=body.skills, model=body.model, name=body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("create_teammate failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    return {"operator": operator, "teammate": mate}


@teammate_router.get("/api/workforce/teammates")
def list_teammates(status: str = "", specialization: str = "",
                   operator: str = Depends(_require_operator)):
    return {"teammates": get_engine().list_teammates(
        status=status or None, specialization=specialization or None)}


@teammate_router.get("/api/workforce/teammates/{teammate_id}")
def get_teammate(teammate_id: str, operator: str = Depends(_require_operator)):
    mate = get_engine().get_teammate(teammate_id)
    if mate is None:
        raise HTTPException(status_code=404, detail="teammate not found")
    return {"teammate": mate}


class TeammateRetire(BaseModel):
    reason: Optional[str] = None


@teammate_router.post("/api/workforce/teammates/{teammate_id}/retire")
def retire_teammate(teammate_id: str, body: TeammateRetire = TeammateRetire(),
                    operator: str = Depends(_require_operator)):
    """Retire a teammate — operator action from the Command Center."""
    mate = get_engine().retire_teammate(
        teammate_id, reason=body.reason or "operator retirement")
    if mate is None:
        raise HTTPException(status_code=404, detail="teammate not found")
    return {"operator": operator, "teammate": mate}


# ── lifecycle maintenance (§31/§39/§43) ─────────────────────────────────────
class MaintenanceRequest(BaseModel):
    idle_seconds: Optional[float] = None


@teammate_router.get("/api/workforce/capability-gaps")
def list_capability_gaps(mission_id: str = "",
                         operator: str = Depends(_require_operator)):
    return {"gaps": get_engine().list_capability_gaps(
        mission_id=mission_id or None)}


@teammate_router.post("/api/workforce/maintenance")
def run_maintenance(body: MaintenanceRequest = MaintenanceRequest(),
                    operator: str = Depends(_require_operator)):
    """Run the workforce maintenance step: auto-retire (§39) + capability-gap
    resolution (§31/§43). Safe and idempotent; also run by the scheduler."""
    engine = get_engine()
    return {
        "operator": operator,
        "retired": engine.auto_retire(idle_seconds=body.idle_seconds),
        "gaps_resolved": engine.scan_capability_gaps(),
    }


# ── teams ───────────────────────────────────────────────────────────────────
@teammate_router.post("/api/workforce/teams")
def form_team(body: TeamCreate, operator: str = Depends(_require_operator)):
    requirement = body.requirement or body.mission
    if not requirement:
        raise HTTPException(status_code=422, detail="requirement (or mission) is required")
    try:
        team = get_engine().form_team(requirement)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("form_team failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    return {"operator": operator, "team": team}


@teammate_router.get("/api/workforce/teams")
def list_teams(operator: str = Depends(_require_operator)):
    return {"teams": get_engine().list_teams()}


@teammate_router.get("/api/workforce/teams/{team_id}")
def get_team(team_id: str, operator: str = Depends(_require_operator)):
    team = get_engine().get_team(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="team not found")
    return {"team": team}


# ── missions ────────────────────────────────────────────────────────────────
@teammate_router.post("/api/missions")
def create_mission(body: MissionCreate, operator: str = Depends(_require_operator)):
    """Decompose ``goal`` into tasks, assign the team and (optionally) execute."""
    try:
        mission = get_engine().create_mission(
            body.goal, execute=body.execute, background=body.background,
            project_path=body.project_path, team_id=body.team_id,
            skills=body.skills, specialization=body.specialization)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown team: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("create_mission failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    return {"operator": operator, "mission": mission}


@teammate_router.get("/api/missions")
def list_missions(status: str = "", operator: str = Depends(_require_operator)):
    return {"missions": get_engine().list_missions(status=status or None)}


@teammate_router.get("/api/missions/{mission_id}")
def get_mission(mission_id: str, operator: str = Depends(_require_operator)):
    mission = get_engine().get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="mission not found")
    return {"mission": mission}


def reset_engine() -> None:
    """Drop the cached engine (tests / controlled rebuilds)."""
    global _ENGINE
    with _ENGINE_LOCK:
        _ENGINE = None
