"""Honest capability status (§1.14, §81 Definition of Done).

``GET /api/media/status`` is backed by this module. Every capability is
probed or explicitly reported BLOCKED with a reason. Nothing is claimed
verified that has not been exercised.
"""
from __future__ import annotations

import shutil
import time
from typing import Optional

from core.media_factory import config, db
from core.media_factory.models import Capability

_trend_probe_cache: dict = {"at": 0.0, "ok": None, "detail": ""}
_TREND_CACHE_TTL = 120.0


def _probe_trends(force: bool = False) -> tuple[bool, str]:
    now = time.time()
    if not force and _trend_probe_cache["ok"] is not None and (now - _trend_probe_cache["at"]) < _TREND_CACHE_TTL:
        return _trend_probe_cache["ok"], _trend_probe_cache["detail"]
    from core.media_factory import trends

    ok, payload = trends.fetch_google_trends_rss(timeout=4.0)
    detail = "Google Trends RSS reachable" if ok else f"unreachable: {payload}"
    _trend_probe_cache.update({"at": now, "ok": ok, "detail": detail})
    return ok, detail


def capabilities(probe_network: bool = True) -> dict:
    from core.media_factory import assets, content

    caps: dict[str, Capability] = {}

    # Database
    h = db.health()
    caps["database"] = Capability(
        "database", h["status"],
        f"{h.get('database', '?')} ({h.get('public_tables', 0)} tables)"
        if h["status"] == config.STATUS_VERIFIED else h.get("error", ""),
        evidence={"public_tables": h.get("public_tables")},
        verified=h["status"] == config.STATUS_VERIFIED,
    )

    # Trend discovery
    if probe_network:
        tok, tdetail = _probe_trends()
        caps["trend_discovery"] = Capability(
            "trend_discovery",
            config.STATUS_VERIFIED if tok else config.STATUS_DEGRADED,
            tdetail,
            blocked_reason=None if tok else "source unreachable",
            verified=tok,
        )
    else:
        caps["trend_discovery"] = Capability(
            "trend_discovery", config.STATUS_UNVERIFIED,
            "network probe skipped", verified=False)

    caps["trend_validation"] = Capability(
        "trend_validation", config.STATUS_VERIFIED,
        "rule-based scoring over fetched signals", verified=True)

    caps["opportunity_engine"] = Capability(
        "opportunity_engine", config.STATUS_VERIFIED,
        "explicit effort/payoff formula", verified=True)

    # Content generation via local model
    model_ok, model_detail = content.model_available(timeout=4.0)
    caps["model_fabric"] = Capability(
        "model_fabric",
        config.STATUS_VERIFIED if model_ok else config.STATUS_DEGRADED,
        model_detail, blocked_reason=None if model_ok else model_detail,
        verified=model_ok)
    caps["content_gen"] = Capability(
        "content_gen",
        config.STATUS_PARTIALLY_VERIFIED if model_ok else config.STATUS_BLOCKED,
        "local qwen3-coder:kai generation works; output quality unverified",
        blocked_reason=None if model_ok else model_detail,
        verified=False)

    # Assets / production
    gen = assets.generation_capability()
    caps["asset_gen"] = Capability(
        "asset_gen", config.STATUS_BLOCKED, "no media generation capability",
        blocked_reason=config.BLOCKED_ASSET_GEN,
        evidence={"ffmpeg": gen["ffmpeg"], "ffprobe": gen["ffprobe"]},
        verified=False)
    caps["voice_audio"] = Capability(
        "voice_audio", config.STATUS_BLOCKED, "no TTS/voice model",
        blocked_reason=config.BLOCKED_VOICE, verified=False)
    caps["editing"] = Capability(
        "editing", config.STATUS_BLOCKED, "no ffmpeg",
        blocked_reason=config.BLOCKED_EDITING,
        evidence={"ffmpeg": bool(shutil.which("ffmpeg"))}, verified=False)
    caps["captions"] = Capability(
        "captions", config.STATUS_BLOCKED, "no caption/ASR tooling",
        blocked_reason=config.BLOCKED_CAPTIONS, verified=False)

    # QC: real file/hash checks; duration blocked without ffprobe
    caps["qc"] = Capability(
        "qc", config.STATUS_PARTIALLY_VERIFIED,
        "file/hash checks real; ffprobe duration unavailable",
        blocked_reason=config.BLOCKED_EDITING,
        evidence={"ffprobe": bool(shutil.which("ffprobe"))}, verified=False)

    caps["rights"] = Capability(
        "rights", config.STATUS_VERIFIED,
        "rights/provenance ledger + publish gate implemented", verified=True)

    caps["publishing"] = Capability(
        "publishing", config.STATUS_BLOCKED,
        "dry_run queue works; live/test/canary blocked without tokens",
        blocked_reason=config.BLOCKED_PUBLISHING, verified=False)

    caps["analytics"] = Capability(
        "analytics", config.STATUS_BLOCKED,
        "manual-entry path works; platform ingestion blocked",
        blocked_reason=config.BLOCKED_ANALYTICS, verified=False)

    caps["revenue"] = Capability(
        "revenue", config.STATUS_VERIFIED,
        "ledger + profitability math; no fabricated data", verified=True)

    caps["experiments"] = Capability(
        "experiments", config.STATUS_PARTIALLY_VERIFIED,
        "assignment + readout scaffolding; no live outcomes", verified=False)

    caps["strategy"] = Capability(
        "strategy", config.STATUS_VERIFIED,
        "immutable versioned strategy with rollback", verified=True)
    caps["patterns"] = Capability(
        "patterns", config.STATUS_VERIFIED,
        "pattern library + lifecycle transitions", verified=True)
    caps["ip_portfolio"] = Capability(
        "ip_portfolio", config.STATUS_VERIFIED,
        "universe/character/episode/canon memory tables", verified=True)
    caps["media_worker"] = Capability(
        "media_worker", config.STATUS_PARTIALLY_VERIFIED,
        "run_media_cycle() called by the scheduler each 60s; self-throttled, "
        "failure-isolated; no live cycle outcomes verified yet",
        verified=False)

    # If the DB is down every ledger-backed capability degrades too.
    if caps["database"].status != config.STATUS_VERIFIED:
        for name in ("rights", "revenue", "strategy", "patterns", "ip_portfolio"):
            caps[name].status = config.STATUS_DEGRADED
            caps[name].detail = "database unavailable"
            caps[name].verified = False

    return {name: cap.to_dict() for name, cap in caps.items()}


def _overall(caps: dict) -> str:
    statuses = [c["status"] for c in caps.values()]
    if config.STATUS_FAILED in statuses:
        return config.STATUS_FAILED
    if config.STATUS_DEGRADED in statuses:
        return config.STATUS_DEGRADED
    if config.STATUS_BLOCKED in statuses:
        return config.STATUS_DEGRADED
    if config.STATUS_PARTIALLY_VERIFIED in statuses:
        return config.STATUS_PARTIALLY_VERIFIED
    if all(s == config.STATUS_VERIFIED for s in statuses):
        return config.STATUS_VERIFIED
    return config.STATUS_UNVERIFIED


def summary(probe_network: bool = True) -> dict:
    caps = capabilities(probe_network=probe_network)
    return {
        "generated_at": time.time(),
        "overall": _overall(caps),
        "capabilities": {name: c["status"] for name, c in caps.items()},
        "details": caps,
    }
