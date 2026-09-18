"""FastAPI surface for the Media Revenue Factory (§57).

Mount with ``app.include_router(media_router)`` and call ``install(app)`` once
to register the consistent error handlers.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.media_factory import (
    analytics as analytics_mod,
    assets as assets_mod,
    config,
    content as content_mod,
    db,
    experiments as experiments_mod,
    notify,
    opportunities as opportunities_mod,
    patterns as patterns_mod,
    publishing as publishing_mod,
    revenue as revenue_mod,
    rights as rights_mod,
    status as status_mod,
    strategy as strategy_mod,
    trends as trends_mod,
    worker as worker_mod,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media", tags=["media"])


class MediaError(Exception):
    def __init__(self, status_code: int, code: str, message: str,
                 details: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _fail(status_code: int, code: str, message: str, details: Optional[dict] = None):
    raise MediaError(status_code, code, message, details)


def media_operator(
    authorization: Optional[str] = Header(default=None),
    x_kai_session: Optional[str] = Header(default=None),
) -> str:
    """Reuse the orchestrator authz when MEDIA_AUTH_REQUIRED is on."""
    if not config.auth_required():
        return "media-operator"
    try:
        from core.authz import _require_write_capability

        return _require_write_capability("media.write")(authorization, x_kai_session)
    except Exception:  # noqa: BLE001
        _fail(403, "forbidden", "media write capability required")


def _page(rows: list[dict], limit: int, offset: int) -> dict:
    return {"data": rows, "count": len(rows), "limit": limit, "offset": offset}


def _serialize(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        out.append({k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in row.items()})
    return out


# ── Pydantic request models ────────────────────────────────────────────────
class TrendScanRequest(BaseModel):
    geo: Optional[str] = Field(default=None, max_length=8)


class ContentCreateRequest(BaseModel):
    opportunity_id: int
    kind: str = Field(default="short", max_length=40)
    account_id: Optional[int] = None


class PublishingEnqueueRequest(BaseModel):
    content_id: int
    account_id: Optional[int] = None
    platform_id: Optional[int] = None
    mode: str = Field(default="dry_run", pattern="^(dry_run|test|canary|live)$")
    dispatch: bool = False
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


class RevenueCreateRequest(BaseModel):
    amount: float = Field(ge=0)
    content_id: Optional[int] = None
    pattern_id: Optional[int] = None
    strategy_version: Optional[int] = None
    platform_id: Optional[int] = None
    source: str = Field(default="manual", max_length=80)
    currency: str = Field(default="USD", max_length=8)
    occurred_at: Optional[str] = None
    evidence: Optional[dict] = None
    verified: bool = False


class CostCreateRequest(BaseModel):
    amount: float = Field(ge=0)
    content_id: Optional[int] = None
    category: str = Field(default="compute", max_length=80)
    currency: str = Field(default="USD", max_length=8)
    occurred_at: Optional[str] = None
    evidence: Optional[dict] = None


class ExperimentCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    name: Optional[str] = None
    hypothesis: Optional[str] = None
    metric: str = Field(default="views", max_length=60)
    variant_a: Optional[str] = None
    variant_b: Optional[str] = None


class StrategyCreateRequest(BaseModel):
    strategy: dict
    evidence: Optional[dict] = None
    activate: bool = False
    approved_by: Optional[str] = None


# ── Status / dashboard ─────────────────────────────────────────────────────
@router.get("/status")
def get_status(probe_network: bool = Query(default=True)):
    return status_mod.summary(probe_network=probe_network)


@router.get("/dashboard")
def get_dashboard():
    counts = {}
    for name in ("trends", "opportunities", "content", "assets", "publishing_jobs",
                 "analytics", "revenue", "costs", "patterns", "experiments",
                 "factories", "policies", "universes", "characters", "episodes"):
        try:
            counts[name] = db.count(name)
        except Exception:  # noqa: BLE001
            counts[name] = None

    content_by_state = _serialize(db.query(
        "SELECT state, count(*) AS n FROM content GROUP BY state ORDER BY n DESC"))
    latest_cycle = db.query_one(
        "SELECT * FROM media_events WHERE stage = 'cycle' ORDER BY created_at DESC LIMIT 1")

    rev = db.query_one(
        "SELECT COALESCE(sum(amount) FILTER (WHERE verified),0) AS verified, "
        "COALESCE(sum(amount) FILTER (WHERE NOT verified),0) AS unverified FROM revenue")
    cost = db.query_one("SELECT COALESCE(sum(amount),0) AS total FROM costs")
    profitability = revenue_mod.compute_profitability(
        db.query("SELECT amount, verified FROM revenue"),
        db.query("SELECT amount FROM costs"),
    )

    caps = status_mod.capabilities(probe_network=False)
    blocked = [
        {"capability": name, "reason": c["blocked_reason"]}
        for name, c in caps.items() if c["status"] == config.STATUS_BLOCKED
    ]
    return {
        "generated_at": time.time(),
        "counts": counts,
        "content_by_state": content_by_state,
        "latest_cycle": latest_cycle,
        "revenue": _one(rev),
        "cost": _one(cost),
        "profitability": profitability,
        "capabilities": {name: c["status"] for name, c in caps.items()},
        "blocked": blocked,
    }


def _one(row: Optional[dict]) -> dict:
    return _serialize([row])[0] if row else {}


# ── Trends ─────────────────────────────────────────────────────────────────
@router.get("/trends")
def list_trends(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return _page(_serialize(trends_mod.latest(limit, offset)), limit, offset)


@router.post("/trends")
def scan_trends(payload: TrendScanRequest, operator: str = Depends(media_operator)):
    result = trends_mod.discover(geo=payload.geo)
    db.audit("api.trends.scan", actor=operator, payload=result)
    return result


# ── Content / production ───────────────────────────────────────────────────
@router.get("/content")
def list_content(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return _page(_serialize(content_mod.latest(limit, offset)), limit, offset)


@router.post("/content")
def create_content(payload: ContentCreateRequest, operator: str = Depends(media_operator)):
    opportunity = db.query_one("SELECT * FROM opportunities WHERE id = %s",
                               (payload.opportunity_id,))
    if not opportunity:
        _fail(404, "not_found", f"opportunity {payload.opportunity_id} not found")
    result = content_mod.create_from_opportunity(
        opportunity, content_kind=payload.kind, account_id=payload.account_id)
    db.audit("api.content.create", entity_type="content",
             entity_id=result.get("content_id"), actor=operator, payload=result)
    return result


@router.get("/production")
def get_production(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    contents = _serialize(content_mod.latest(limit, offset))
    assets = _serialize(assets_mod.latest(limit, offset))
    jobs = _serialize(publishing_mod.latest(limit, offset))
    return {
        "content": contents,
        "assets": assets,
        "publishing_jobs": jobs,
        "generation": assets_mod.generation_capability(),
        "counts": {"content": len(contents), "assets": len(assets), "jobs": len(jobs)},
    }


# ── Publishing ─────────────────────────────────────────────────────────────
@router.get("/publishing")
def list_publishing(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return _page(_serialize(publishing_mod.latest(limit, offset)), limit, offset)


@router.post("/publishing")
def enqueue_publishing(payload: PublishingEnqueueRequest,
                       operator: str = Depends(media_operator)):
    job = publishing_mod.enqueue(
        payload.content_id,
        account_id=payload.account_id,
        platform_id=payload.platform_id,
        mode=payload.mode,
        idempotency_key=payload.idempotency_key,
    )
    result = {"job": job}
    if payload.dispatch:
        result["dispatch"] = publishing_mod.dispatch(job["id"])
    db.audit("api.publishing.enqueue", entity_type="publishing_job",
             entity_id=job.get("id"), actor=operator, payload=result)
    return result


# ── Analytics / revenue / costs / profitability ────────────────────────────
@router.get("/analytics")
def list_analytics(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return {
        "data": _serialize(analytics_mod.latest(limit, offset)),
        "availability": analytics_mod.availability(),
        "limit": limit,
        "offset": offset,
    }


@router.get("/revenue")
def list_revenue(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return _page(_serialize(revenue_mod.latest_revenue(limit, offset)), limit, offset)


@router.post("/revenue")
def create_revenue(payload: RevenueCreateRequest, operator: str = Depends(media_operator)):
    result = revenue_mod.record_revenue(
        payload.amount,
        content_id=payload.content_id,
        pattern_id=payload.pattern_id,
        strategy_version=payload.strategy_version,
        platform_id=payload.platform_id,
        source=payload.source,
        currency=payload.currency,
        occurred_at=payload.occurred_at,
        evidence=payload.evidence,
        verified=payload.verified,
    )
    db.audit("api.revenue.create", entity_type="content", entity_id=payload.content_id,
             actor=operator, payload=result)
    return result


@router.get("/costs")
def list_costs(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return _page(_serialize(revenue_mod.latest_costs(limit, offset)), limit, offset)


@router.post("/costs")
def create_cost(payload: CostCreateRequest, operator: str = Depends(media_operator)):
    result = revenue_mod.record_cost(
        payload.amount,
        content_id=payload.content_id,
        category=payload.category,
        currency=payload.currency,
        occurred_at=payload.occurred_at,
        evidence=payload.evidence,
    )
    db.audit("api.cost.create", entity_type="content", entity_id=payload.content_id,
             actor=operator, payload=result)
    return result


@router.get("/profitability")
def get_profitability(
    scope_type: str = Query(default="global", pattern="^(global|content|pattern|strategy)$"),
    scope_id: Optional[int] = Query(default=None),
):
    revenues, costs = revenue_mod._filter(scope_type, scope_id)
    result = revenue_mod.compute_profitability(revenues, costs)
    result.update({"scope_type": scope_type, "scope_id": scope_id,
                   "revenue_count": len(revenues), "cost_count": len(costs)})
    return result


@router.get("/attribution")
def get_attribution(content_id: Optional[int] = Query(default=None)):
    return revenue_mod.attribution(content_id)


# ── Experiments / strategy / patterns ──────────────────────────────────────
@router.get("/experiments")
def list_experiments(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return _page(_serialize(experiments_mod.latest(limit, offset)), limit, offset)


@router.post("/experiments")
def create_experiment(payload: ExperimentCreateRequest,
                      operator: str = Depends(media_operator)):
    row = experiments_mod.create(
        payload.key, name=payload.name, hypothesis=payload.hypothesis,
        metric=payload.metric, variant_a=payload.variant_a, variant_b=payload.variant_b)
    db.audit("api.experiment.create", entity_type="experiment",
             entity_id=row.get("id"), actor=operator, payload={"key": payload.key})
    notify.emit("info", "Media experiment created",
                f"{payload.key} metric={payload.metric}")
    return _one(row)


@router.get("/experiments/{experiment_id}/readout")
def experiment_readout(experiment_id: int):
    return experiments_mod.readout(experiment_id)


@router.get("/strategy")
def list_strategy(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    rows = _serialize(strategy_mod.latest(limit, offset))
    return {"data": rows, "active": _one(strategy_mod.active()), "limit": limit, "offset": offset}


@router.post("/strategy")
def create_strategy(payload: StrategyCreateRequest, operator: str = Depends(media_operator)):
    row = strategy_mod.create_version(
        payload.strategy, evidence=payload.evidence,
        approved_by=payload.approved_by or operator)
    result = {"version": _one(row)}
    if payload.activate:
        result["activation"] = strategy_mod.activate(row["version"],
                                                     approved_by=payload.approved_by or operator)
    db.audit("api.strategy.create", entity_type="strategy_version",
             entity_id=row.get("id"), actor=operator, payload={"version": row["version"]})
    return result


@router.post("/strategy/{version}/activate")
def activate_strategy(version: int, operator: str = Depends(media_operator)):
    result = strategy_mod.activate(version, approved_by=operator)
    db.audit("api.strategy.activate", actor=operator, payload=result)
    return result


@router.get("/patterns")
def list_patterns(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return _page(_serialize(patterns_mod.latest(limit, offset)), limit, offset)


@router.get("/patterns/{pattern_id}/lifecycle")
def pattern_lifecycle(pattern_id: int):
    return {"data": _serialize(patterns_mod.lifecycle(pattern_id))}


# ── Rights / workers / models / policies / audit / IP / factories ──────────
@router.get("/rights")
def list_rights(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return _page(_serialize(rights_mod.latest(limit, offset)), limit, offset)


@router.get("/workers")
def list_workers(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    rows = _serialize(db.query(
        "SELECT * FROM workers ORDER BY created_at DESC LIMIT %s OFFSET %s", (limit, offset)))
    return {
        "data": rows,
        "media_worker": {"callable": "core.media_factory.worker.run_media_cycle",
                         "scheduler_registered": worker_mod.SCHEDULER_REGISTERED},
        "limit": limit, "offset": offset,
    }


@router.get("/models")
def list_models(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    rows = _serialize(db.query(
        "SELECT * FROM models ORDER BY created_at DESC LIMIT %s OFFSET %s", (limit, offset)))
    available, detail = content_mod.model_available(timeout=4.0)
    registry = None
    try:
        from core.model_registry import build_registry

        reg = build_registry()
        registry = {
            "counts": reg.get("counts", {}),
            "models": sorted(reg.get("models", {}).keys()),
        }
    except Exception as exc:  # noqa: BLE001 - registry is a read-only projection
        logger.warning("model registry unavailable: %s", type(exc).__name__)
    return {"data": rows,
            "live": {"model": config.LLM_MODEL, "available": available, "detail": detail},
            "registry": registry,
            "limit": limit, "offset": offset}


@router.get("/policies")
def list_policies(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return _page(_serialize(db.query(
        "SELECT * FROM policies ORDER BY key LIMIT %s OFFSET %s", (limit, offset))),
        limit, offset)


@router.get("/audit")
def list_audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    event_type: Optional[str] = Query(default=None, max_length=120),
):
    if event_type:
        rows = db.query(
            "SELECT * FROM audit_events WHERE event_type = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (event_type, limit, offset))
    else:
        rows = db.query(
            "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (limit, offset))
    return _page(_serialize(rows), limit, offset)


@router.get("/ip")
def list_ip(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    rows = _serialize(db.query(
        "SELECT * FROM ip_portfolio ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (limit, offset)))
    universes = db.count("universes")
    characters = db.count("characters")
    episodes = db.count("episodes")
    return {"data": rows, "counts": {"universes": universes,
                                     "characters": characters, "episodes": episodes},
            "limit": limit, "offset": offset}


@router.get("/factories")
def list_factories():
    return {"data": _serialize(db.query("SELECT * FROM factories ORDER BY key"))}


# ── Error handling ─────────────────────────────────────────────────────────
def _error_response(status_code: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status_code,
                        content={"error": {"code": code, "message": message,
                                           "details": details or {}}})


async def _media_error_handler(request, exc: MediaError):
    return _error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _validation_handler(request, exc: RequestValidationError):
    if request.url.path.startswith("/api/media"):
        return _error_response(422, "validation_error", "request validation failed",
                               {"errors": exc.errors()})
    from fastapi.exception_handlers import request_validation_exception_handler

    return await request_validation_exception_handler(request, exc)


async def _db_error_handler(request, exc: Exception):
    logger.warning("media db error: %s", type(exc).__name__)
    return _error_response(503, "database_unavailable",
                           "media database unavailable or not migrated",
                           {"type": type(exc).__name__})


def install(app) -> None:
    """Register media error handlers on the host FastAPI app."""
    import psycopg2

    app.add_exception_handler(MediaError, _media_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(psycopg2.Error, _db_error_handler)
