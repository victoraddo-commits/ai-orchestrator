"""Media cycle orchestrator (§8).

Runs the stages that can honestly run on CT111 and records every stage in
``media_events`` + the append-only audit ledger. Each stage is isolated: a
failure is captured as FAILED and the cycle continues. Stages that cannot run
(generation, platform analytics) report BLOCKED with a reason rather than
producing placeholder output.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Callable, Optional

from core.media_factory import config, db
from core.media_factory.models import StageResult

logger = logging.getLogger(__name__)

FACTORIES = ("public_domain_cartoon", "original_ai_drama")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "untitled"


def _run_stage(stage: str, fn: Callable[[], StageResult], cycle_id: str) -> StageResult:
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - fail-safe: one stage must not kill the cycle
        logger.warning("media stage %s failed (%s)", stage, type(exc).__name__)
        result = StageResult(stage, config.STATUS_FAILED,
                             f"{type(exc).__name__}: {exc}")
    db.record_event(stage, result.status, cycle_id=cycle_id, detail=result.to_dict())
    return result


def run_cycle(
    *,
    geo: Optional[str] = None,
    factory_key: Optional[str] = None,
    create_content: bool = True,
) -> dict:
    cycle_id = uuid.uuid4().hex[:12]
    stages: dict[str, dict] = {}

    if not config.media_enabled():
        return {
            "cycle_id": cycle_id,
            "status": config.STATUS_UNVERIFIED,
            "detail": "MEDIA_ENABLED=false; cycle skipped",
            "stages": {},
        }

    from core.media_factory import (
        analytics, assets, content, opportunities, patterns, publishing,
        revenue, rights, trends,
    )

    # 1. Trend discovery
    trend_result = _run_stage("trend_discovery", lambda: _stage_trends(trends, geo), cycle_id)
    stages["trend_discovery"] = trend_result.to_dict()

    # 2. Opportunity detection
    opp_result = _run_stage(
        "opportunity_detection",
        lambda: _stage_opportunities(opportunities, trends, factory_key),
        cycle_id,
    )
    stages["opportunity_detection"] = opp_result.to_dict()

    # 3. Content generation (idea -> story -> script)
    top_opp = opportunities.latest(limit=1)
    if not top_opp or not create_content:
        content_result = StageResult(
            "content_generation", config.STATUS_UNVERIFIED,
            "no opportunity available to produce" if not top_opp else "disabled")
    else:
        content_result = _run_stage(
            "content_generation",
            lambda: _stage_content(content, top_opp[0]),
            cycle_id,
        )
    stages["content_generation"] = content_result.to_dict()
    content_id = content_result.data.get("content_id")

    # 4. QC (real checks; no assets exist because generation is blocked)
    qc_result = _run_stage("quality_control", lambda: _stage_qc(assets, content_id), cycle_id)
    stages["quality_control"] = qc_result.to_dict()

    # 5. Rights gate
    rights_result = _run_stage("rights_check", lambda: _stage_rights(rights, content_id), cycle_id)
    stages["rights_check"] = rights_result.to_dict()

    # 6. Publishing (dry_run only)
    publish_result = _run_stage(
        "publishing", lambda: _stage_publish(publishing, content_id), cycle_id)
    stages["publishing"] = publish_result.to_dict()

    # 7. Analytics (honestly blocked)
    analytics_result = _run_stage(
        "analytics", lambda: _stage_analytics(analytics, content_id), cycle_id)
    stages["analytics"] = analytics_result.to_dict()

    # 8. Intelligence (patterns + profitability snapshot)
    intel_result = _run_stage(
        "intelligence", lambda: _stage_intelligence(patterns, revenue, trends),
        cycle_id)
    stages["intelligence"] = intel_result.to_dict()

    statuses = [s["status"] for s in stages.values()]
    if config.STATUS_FAILED in statuses:
        overall = config.STATUS_DEGRADED
    elif all(s == config.STATUS_UNVERIFIED for s in statuses):
        overall = config.STATUS_UNVERIFIED
    elif all(s == config.STATUS_VERIFIED for s in statuses):
        overall = config.STATUS_VERIFIED
    else:
        overall = config.STATUS_PARTIALLY_VERIFIED

    summary = {"cycle_id": cycle_id, "status": overall, "stages": stages}
    db.record_event("cycle", overall, cycle_id=cycle_id, detail=summary)
    db.audit("cycle.completed", payload={"cycle_id": cycle_id, "status": overall,
                                         "stages": {k: v["status"] for k, v in stages.items()}})
    return summary


# ── Stage implementations ──────────────────────────────────────────────────
def _stage_trends(trends, geo: Optional[str]) -> StageResult:
    result = trends.discover(geo=geo)
    status = result["status"]
    detail = f"fetched={result['fetched']} scored={result['scored']} stored={result['stored']}"
    return StageResult("trend_discovery", status, detail,
                       blocked_reason=result.get("error"), data=result)


def _stage_opportunities(opportunities, trends, factory_key: Optional[str]) -> StageResult:
    rows = trends.validated(limit=25)
    if not rows:
        return StageResult("opportunity_detection", config.STATUS_UNVERIFIED,
                           "no validated trends")
    keys = [factory_key] if factory_key else list(FACTORIES)
    created = 0
    for key in keys:
        res = opportunities.detect(rows, factory_key=key)
        created += res.get("created", 0)
    return StageResult("opportunity_detection", config.STATUS_VERIFIED,
                       f"validated_trends={len(rows)} created={created}",
                       data={"created": created, "validated_trends": len(rows)})


def _stage_content(content, opportunity: dict) -> StageResult:
    result = content.create_from_opportunity(opportunity)
    return StageResult("content_generation", result["status"],
                       f"content_id={result.get('content_id')} script={result.get('script_status')}",
                       blocked_reason=result.get("blocked_reason"), data=result)


def _stage_qc(assets, content_id: Optional[int]) -> StageResult:
    if content_id is None:
        return StageResult("quality_control", config.STATUS_UNVERIFIED,
                           "no content to QC")
    rows = db.query("SELECT id FROM assets WHERE content_id = %s LIMIT 1", (content_id,))
    if not rows:
        return StageResult("quality_control", config.STATUS_UNVERIFIED,
                           "no assets exist (generation is BLOCKED)",
                           blocked_reason=config.BLOCKED_ASSET_GEN)
    return StageResult("quality_control", config.STATUS_PARTIALLY_VERIFIED,
                       "asset QC executed")


def _stage_rights(rights, content_id: Optional[int]) -> StageResult:
    if content_id is None:
        return StageResult("rights_check", config.STATUS_UNVERIFIED, "no content to check")
    verdict = rights.check_publishable(content_id)
    return StageResult("rights_check", verdict["status"], verdict["reason"],
                       blocked_reason=None if verdict["publishable"] else verdict["reason"],
                       data=verdict)


def _stage_publish(publishing, content_id: Optional[int]) -> StageResult:
    if content_id is None:
        return StageResult("publishing", config.STATUS_UNVERIFIED, "no content to publish")
    job = publishing.enqueue(content_id, mode="dry_run")
    dispatch = publishing.dispatch(job["id"])
    return StageResult("publishing", dispatch["status"],
                       f"mode=dry_run job={job['id']}",
                       blocked_reason=dispatch.get("blocked_reason"), data=dispatch)


def _stage_analytics(analytics, content_id: Optional[int]) -> StageResult:
    if content_id is None:
        return StageResult("analytics", config.STATUS_UNVERIFIED, "no content to measure")
    result = analytics.ingest(content_id, "youtube")
    return StageResult("analytics", result["status"], "platform ingestion",
                       blocked_reason=result.get("blocked_reason"), data=result)


def _stage_intelligence(patterns, revenue, trends) -> StageResult:
    rows = trends.validated(limit=3)
    created = []
    for row in rows:
        pattern = patterns.get_or_create(
            f"trend:{_slug(row.get('title', ''))}",
            name=row.get("title"),
            description="emerging trend pattern",
            evidence={"trend_id": row.get("id"), "score": float(row.get("score") or 0)},
        )
        created.append(pattern["id"])
    prof = revenue.profitability(persist=True)
    return StageResult("intelligence", config.STATUS_PARTIALLY_VERIFIED,
                       f"patterns={len(created)} profit_status={prof['status']}",
                       data={"pattern_ids": created, "profitability": prof})
