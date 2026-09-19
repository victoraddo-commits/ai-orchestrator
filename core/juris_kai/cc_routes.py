"""Juris Kai Command Center control-plane API (Part B).

A dedicated FastAPI router that turns the Command Center's Juris Kai surface
into a real control plane: health, corpus browse/ingest, accounts, referrals,
bot service control, model routing, latency metrics and cache management.

Auth:
  * Reads  — bridge token OR an operator session (``require_cc_read``), so the
    Command Center SPA (X-Kai-Session) works without the raw bridge token.
  * Writes — bridge token OR a session holding ``juris.admin``, plus a
    per-client rate limit (same policy as the existing juris admin endpoints).

Everything is read-only over existing sources of truth (account manager,
legal_brain_client, ai_router) — no duplicated business logic.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

import core.authz as authz
from core.payments.keys import PaymentConfigError
from core.payments.paystack import PaystackError

logger = logging.getLogger("juris_kai.cc_routes")

router = APIRouter(tags=["juris-cc"])

_BOT_HEALTH_CANDIDATES = (
    Path("/project/ai-orchestrator/memory/juris-kai-health"),
    Path("/opt/ai-orchestrator/memory/juris-kai-health"),
)
_BOT_SERVICE = "juris-kai.service"
_STREAM_TIMEOUT_S = float(os.environ.get("JURIS_KAI_CC_STREAM_TIMEOUT", "300"))


# ── auth dependencies ─────────────────────────────────────────────────────

def _is_bridge(authorization: str | None) -> bool:
    try:
        from core.bridge_auth import _load_api_token
    except Exception:
        return False
    if not authorization:
        return False
    return hmac.compare_digest(
        authorization.encode(), f"Bearer {_load_api_token()}".encode())


def _operator_identity(authorization: str | None) -> str | None:
    if _is_bridge(authorization):
        try:
            from core.bridge_auth import BRIDGE_OPERATOR
            return BRIDGE_OPERATOR
        except Exception:
            return "bridge"
    return None


def _proxy_identity(x_kai_user: str | None,
                    x_kai_user_id: str | None) -> str | None:
    """The CC browser reaches the API through the auth proxy, which injects a
    verified ``X-Kai-User``/``X-Kai-User-Id`` identity pair. Every other CC
    router (``cc_modules`` / ``cc_extra_routes``) accepts it as an operator, so
    the juris CC surface must too — otherwise its panels 401 while the rest of
    the Command Center works. Both headers are required to avoid a stray one
    being treated as identity."""
    if x_kai_user and x_kai_user_id:
        return f"auth-proxy:{x_kai_user_id}"
    return None


def require_cc_read(
    authorization: str | None = Header(default=None),
    x_kai_session: str | None = Header(default=None),
    x_kai_user: str | None = Header(default=None),
    x_kai_user_id: str | None = Header(default=None),
) -> str:
    """Read gate: bridge token, an operator session, or auth-proxy identity."""
    operator = _operator_identity(authorization)
    if operator:
        return operator
    if x_kai_session and (authz.check_capability(x_kai_session, "kai.command")
                          or authz.check_capability(x_kai_session, "juris.admin")):
        return x_kai_session
    proxy = _proxy_identity(x_kai_user, x_kai_user_id)
    if proxy:
        return proxy
    raise HTTPException(status_code=401, detail="Missing or invalid credentials")


def require_juris_write(
    authorization: str | None = Header(default=None),
    x_kai_session: str | None = Header(default=None),
    x_kai_user: str | None = Header(default=None),
    x_kai_user_id: str | None = Header(default=None),
) -> str:
    """Write gate: bridge token, a session holding ``juris.admin``, or
    auth-proxy identity (same policy as the rest of the CC)."""
    operator = _operator_identity(authorization)
    if operator:
        return operator
    if x_kai_session:
        if authz.check_capability(x_kai_session, "juris.admin"):
            return x_kai_session
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    proxy = _proxy_identity(x_kai_user, x_kai_user_id)
    if proxy:
        return proxy
    raise HTTPException(status_code=401, detail="Missing or invalid credentials")


# ── admin rate limiting (mirrors core.api._check_admin_rate_limit) ─────────

_RATE_MAX = 20
_RATE_WINDOW = 60
_rate_state: dict[str, list[float]] = defaultdict(list)


def _rate_limit(request: Request | None, operator: str = "") -> None:
    if request is None:
        return
    client = request.client.host if request.client else "unknown"
    key = f"{client}:{operator}" if operator else client
    now = time.monotonic()
    _rate_state[key] = [t for t in _rate_state[key] if now - t < _RATE_WINDOW]
    if len(_rate_state[key]) >= _RATE_MAX:
        raise HTTPException(status_code=429, detail="Too many admin requests")
    _rate_state[key].append(now)


def _log_admin(operator: str, action: str, meta: dict | None = None) -> None:
    try:
        import json as _json
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        mgr.db.execute(
            """INSERT INTO juris_security_log
               (telegram_id, event_type, details, ip_address)
               VALUES (?, 'admin_action', ?, ?)""",
            (operator, _json.dumps({"action": action, **(meta or {})}), "cc-api"),
        )
        mgr.db.commit()
    except Exception:
        pass  # auditing must never break the endpoint


# ── health / metrics helpers ──────────────────────────────────────────────

def _service_state(name: str) -> str:
    try:
        proc = subprocess.run(["systemctl", "is-active", name],
                              capture_output=True, text=True, timeout=8)
        return (proc.stdout or "").strip() or "unknown"
    except Exception as exc:
        return f"error: {exc}"


def _bot_health() -> dict:
    state = _service_state(_BOT_SERVICE)
    health_file = None
    age = None
    now = time.time()
    for path in _BOT_HEALTH_CANDIDATES:
        try:
            if path.exists():
                health_file = str(path)
                age = round(now - path.stat().st_mtime, 1)
                break
        except Exception:
            continue
    return {
        "service": _BOT_SERVICE,
        "state": state,
        "active": state == "active",
        "health_file": health_file,
        "health_file_age_s": age,
    }


def _legal_brain_health() -> dict:
    try:
        from core import legal_brain_client as lb
        return {"ok": True, **lb.health()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _juris_routing() -> dict:
    try:
        from core.ai.ai_router import provider_chain_report
        rep = provider_chain_report()
    except Exception as exc:
        return {"error": str(exc)}
    chains = {k: v for k, v in (rep.get("chains") or {}).items()
              if k.startswith("juris_")}
    defaults = {k: v for k, v in (rep.get("default_chains") or {}).items()
                if k.startswith("juris_")}
    providers: dict[str, dict] = {}
    try:
        from core.model_registry import endpoint_for, node_for
        for chain in chains.values():
            for name in chain:
                providers[name] = {"endpoint": endpoint_for(name),
                                   "node": node_for(name)}
    except Exception:
        pass
    budgets = {}
    try:
        from core.juris_kai.prompt import TASK_MAX_TOKENS, DEFAULT_MAX_TOKENS
        budgets = dict(TASK_MAX_TOKENS)
        budgets["_default"] = DEFAULT_MAX_TOKENS
    except Exception:
        pass
    return {
        "chains": chains,
        "default_chains": defaults,
        "providers": providers,
        "provider_state": rep.get("provider_state") or {},
        "task_budgets": budgets,
    }


def _juris_metrics(limit: int = 200) -> dict:
    """Aggregate latency/success from the local usage history for juris_*."""
    try:
        from core.ai.ai_router import get_usage_history
        hist = [e for e in get_usage_history()
                if str(e.get("task_type", "")).startswith("juris_")][-limit:]
    except Exception as exc:
        return {"error": str(exc)}
    durations = [e.get("duration_ms", 0) for e in hist
                 if isinstance(e.get("duration_ms"), (int, float))]
    successes = [e for e in hist if e.get("success")]
    by_task: dict[str, dict] = {}
    for entry in hist:
        task = entry.get("task_type", "?")
        bucket = by_task.setdefault(task, {"count": 0, "success": 0,
                                           "duration_ms_sum": 0})
        bucket["count"] += 1
        bucket["success"] += 1 if entry.get("success") else 0
        bucket["duration_ms_sum"] += int(entry.get("duration_ms") or 0)
    for bucket in by_task.values():
        bucket["avg_ms"] = round(bucket["duration_ms_sum"] / max(bucket["count"], 1), 1)
    return {
        "samples": len(hist),
        "success_rate": round(len(successes) / len(hist), 4) if hist else None,
        "avg_ms": round(sum(durations) / len(durations), 1) if durations else None,
        "by_task": by_task,
    }


def _cache_stats() -> dict:
    try:
        from core.juris_kai.cache import cache_stats
        return cache_stats()
    except Exception as exc:
        return {"error": str(exc)}


# ── health / routing / metrics ────────────────────────────────────────────

@router.get("/api/juris-kai/health")
def cc_health(_: str = Depends(require_cc_read)):
    return {
        "bot": _bot_health(),
        "legal_brain": _legal_brain_health(),
        "routing": _juris_routing(),
        "metrics": _juris_metrics(),
        "cache": _cache_stats(),
    }


@router.get("/api/juris-kai/routing")
def cc_routing(_: str = Depends(require_cc_read)):
    return _juris_routing()


@router.get("/api/juris-kai/metrics")
def cc_metrics(limit: int = 200, _: str = Depends(require_cc_read)):
    return {"metrics": _juris_metrics(limit=limit), "cache": _cache_stats()}


# ── cache (session-readable alias; clear reuses Part A endpoint) ──────────

@router.get("/api/juris-kai/cc/cache")
def cc_cache(_: str = Depends(require_cc_read)):
    return _cache_stats()


@router.post("/api/juris-kai/cc/cache/clear")
def cc_cache_clear(operator: str = Depends(require_juris_write),
                   request: Request = None):
    """Operator-gated cache purge (Part B alias of the Part A endpoint)."""
    _rate_limit(request, operator)
    try:
        from core.juris_kai.cache import clear_caches
        result = clear_caches()
        _log_admin(operator, "cache_clear", result)
        return {"success": True, **result}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ── corpus ────────────────────────────────────────────────────────────────

@router.get("/api/juris-kai/corpus/stats")
def cc_corpus_stats(_: str = Depends(require_cc_read)):
    try:
        from core import legal_brain_client as lb
        return {"ok": True, "stats": lb.stats()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/juris-kai/corpus/search")
def cc_corpus_search(q: str = "", limit: int = 20,
                     _: str = Depends(require_cc_read)):
    try:
        from core import legal_brain_client as lb
        return {"ok": True, "query": q, "results": lb.search(q, limit=limit)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "results": []}


@router.get("/api/juris-kai/corpus/documents")
def cc_corpus_documents(jurisdiction: str = "", status: str = "",
                        limit: int = 50, _: str = Depends(require_cc_read)):
    try:
        from core import legal_brain_client as lb
        return {"ok": True, "documents": lb.documents(
            jurisdiction=jurisdiction or None, status=status or None, limit=limit)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "documents": []}


@router.get("/api/juris-kai/corpus/document/{doc_id}")
def cc_corpus_document(doc_id: int, _: str = Depends(require_cc_read)):
    from core import legal_brain_client as lb
    out: dict = {"ok": True}
    try:
        out["document"] = lb.get_document(doc_id)
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)
    try:
        out["versions"] = lb.versions(doc_id)
    except Exception:
        out["versions"] = []
    try:
        out["integrity"] = lb.integrity(doc_id)
    except Exception:
        out["integrity"] = {}
    return out


@router.post("/api/juris-kai/corpus/ingest")
def cc_corpus_ingest(body: dict = Body(...),
                     operator: str = Depends(require_juris_write),
                     request: Request = None):
    _rate_limit(request, operator)
    from core import legal_brain_client as lb
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    errors = []
    if len(title) < 3:
        errors.append("title must be at least 3 characters")
    if len(content) < 20:
        errors.append("content must be at least 20 characters")
    if errors:
        return {"success": False, "validation_errors": errors}
    try:
        result = lb.ingest(
            title=title, content=content,
            citation=body.get("citation", ""), court=body.get("court", ""),
            year=body.get("year", 0) or 0, doc_type=body.get("type", ""),
            jurisdiction=body.get("jurisdiction", "ghana") or "ghana",
            source_url=body.get("source_url", ""),
        )
        success = bool(result.get("ok", result.get("id") is not None))
        if success:
            _log_admin(operator, "corpus_ingest",
                       {"title": title, "id": result.get("id")})
        return {"success": success, "result": result}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ── accounts / referrals / usage / activity ───────────────────────────────

@router.get("/api/juris-kai/cc/accounts")
def cc_accounts(q: str = "", tier: str = "", active_only: bool = False,
                page: int = 1, per_page: int = 50,
                _: str = Depends(require_cc_read)):
    from core.juris_kai.accounts import get_account_manager
    from core.juris_kai.dashboard import list_accounts
    try:
        mgr = get_account_manager()
        if q or tier or active_only:
            return mgr.find_accounts(query=q, tier=tier,
                                     active_only=active_only,
                                     page=page, per_page=per_page)
        return list_accounts(page=page, per_page=per_page, active_only=active_only)
    except Exception as exc:
        return {"error": str(exc), "accounts": []}


@router.get("/api/juris-kai/cc/accounts/{account_id}")
def cc_account_detail(account_id: str, _: str = Depends(require_cc_read)):
    from core.juris_kai.dashboard import (
        get_account_detail, get_payment_history, get_usage_log)
    detail = get_account_detail(account_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Account not found")
    detail["payments"] = get_payment_history(account_id=account_id, limit=20)
    detail["usage_log"] = get_usage_log(account_id=account_id, limit=20)
    return detail


@router.get("/api/juris-kai/cc/usage")
def cc_usage(account_id: str = "", limit: int = 100,
             _: str = Depends(require_cc_read)):
    from core.juris_kai.dashboard import get_usage_log
    try:
        return {"usage": get_usage_log(account_id=account_id or None, limit=limit)}
    except Exception as exc:
        return {"error": str(exc), "usage": []}


@router.get("/api/juris-kai/cc/payments")
def cc_payments(account_id: str = "", limit: int = 50,
                _: str = Depends(require_cc_read)):
    from core.juris_kai.dashboard import get_payment_history
    try:
        return {"payments": get_payment_history(
            account_id=account_id or None, limit=limit)}
    except Exception as exc:
        return {"error": str(exc), "payments": []}


@router.get("/api/juris-kai/cc/referrals")
def cc_referrals(limit: int = 100, _: str = Depends(require_cc_read)):
    from core.juris_kai.accounts import get_account_manager
    try:
        return {"referrals": get_account_manager().get_all_referrals(limit=limit)}
    except Exception as exc:
        return {"error": str(exc), "referrals": []}


@router.get("/api/juris-kai/cc/security-log")
def cc_security_log(event_type: str = "", limit: int = 100,
                    _: str = Depends(require_cc_read)):
    from core.juris_kai.accounts import get_account_manager
    try:
        return {"events": get_account_manager().get_security_logs(
            event_type=event_type, limit=limit)}
    except Exception as exc:
        return {"error": str(exc), "events": []}


@router.get("/api/juris-kai/activity")
def cc_activity(limit: int = 50, _: str = Depends(require_cc_read)):
    from core.juris_kai.accounts import get_account_manager
    from core.juris_kai.dashboard import get_usage_log
    out: dict = {}
    try:
        out["security"] = get_account_manager().get_security_logs(limit=limit)
    except Exception as exc:
        out["security"] = []
        out["security_error"] = str(exc)
    try:
        out["usage"] = get_usage_log(limit=limit)
    except Exception as exc:
        out["usage"] = []
        out["usage_error"] = str(exc)
    return out


# ── bot service control (operator-gated) ──────────────────────────────────

@router.get("/api/juris-kai/cc/service")
def cc_service_status(_: str = Depends(require_cc_read)):
    """Lightweight bot-service state for the Bot control tab."""
    return _bot_health()


@router.post("/api/juris-kai/bot/{action}")
@router.post("/api/juris-kai/cc/service/{action}")
def cc_bot_control(action: str, operator: str = Depends(require_juris_write),
                   request: Request = None):
    _rate_limit(request, operator)
    if action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400,
                            detail="action must be start, stop or restart")
    try:
        proc = subprocess.run(["systemctl", action, _BOT_SERVICE],
                              capture_output=True, text=True, timeout=25)
        ok = proc.returncode == 0
        _log_admin(operator, f"bot_{action}", {"ok": ok})
        return {"success": ok, "action": action,
                "stdout": (proc.stdout or "").strip()[:400],
                "stderr": (proc.stderr or "").strip()[:400]}
    except Exception as exc:
        return {"success": False, "action": action, "error": str(exc)}


# ── test query (real local generation, streamed vs blocking) ──────────────

@router.post("/api/juris-kai/test-query")
@router.post("/api/juris-kai/cc/test-query")
def cc_test_query(body: dict = Body(...),
                  operator: str = Depends(require_juris_write),
                  request: Request = None):
    _rate_limit(request, operator)
    query = (body.get("query") or "").strip()
    if not query:
        return {"success": False, "error": "query is required"}
    task_type = body.get("task_type") or "juris_research"
    want_stream = bool(body.get("stream", True))

    from core.juris_kai.prompt import build_prompt, budget_for
    from core.juris_kai import streaming as jstream
    from core.juris_kai.legal_context import (
        query_knowledge_base, build_context_preamble)

    prompt_type = (task_type.replace("juris_", "legal_", 1)
                   if task_type.startswith("juris_") else "legal_research")
    docs = query_knowledge_base(query)
    context = build_context_preamble(docs)
    prompt = build_prompt(prompt_type, query) + context

    started = time.time()
    ttft = None
    text = ""
    model = jstream.DEFAULT_MODEL
    streamed = False
    error = None
    try:
        if want_stream:
            for piece in jstream.stream_chat(prompt, task_type=task_type):
                if ttft is None:
                    ttft = round((time.time() - started) * 1000, 1)
                text += piece
            streamed = True
        else:
            text = jstream.generate(prompt, task_type=task_type)
    except Exception as stream_exc:
        try:
            from core.ai.ai_router import delegate
            result = delegate(prompt, task_type=task_type,
                              capability="text_task")
            text = result.get("response") or ""
            model = result.get("provider") or model
        except Exception as fallback_exc:
            error = f"stream={stream_exc}; fallback={fallback_exc}"

    latency = round((time.time() - started) * 1000, 1)
    if error:
        return {"success": False, "error": error, "latency_ms": latency}
    return {
        "success": True,
        "text": text,
        "model": model,
        "latency_ms": latency,
        "ttft_ms": ttft,
        "streamed": streamed,
        "budget_tokens": budget_for(task_type),
        "context_chunks": len(docs),
        "context_chars": len(context),
    }


# ── pricing (editable tiers) ──────────────────────────────────────────────

@router.get("/api/juris-kai/cc/pricing")
def cc_pricing_get(_: str = Depends(require_cc_read)):
    """Return the effective tiers + per-document page rate (read-gated)."""
    from core.juris_kai import pricing as _pricing

    doc = _pricing.load_pricing()
    return {
        "success": True,
        "tiers": doc.get("tiers") or {},
        "per_document_page_rate_ghs": doc.get("per_document_page_rate_ghs"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "defaults": {
            "tiers": _pricing.DEFAULT_TIERS,
            "per_document_page_rate_ghs": _pricing.DEFAULT_PER_DOCUMENT_PAGE_RATE_GHS,
        },
    }


@router.put("/api/juris-kai/cc/pricing")
def cc_pricing_put(body: dict = Body(...),
                   operator: str = Depends(require_juris_write),
                   request: Request = None):
    """Validate + persist an edited pricing document (write-gated, audited)."""
    from core.juris_kai import pricing as _pricing

    _rate_limit(request, operator)
    tiers = body.get("tiers")
    rate = body.get("per_document_page_rate_ghs")
    errors = []
    if tiers is None:
        errors.append("tiers is required")
    if rate is None:
        errors.append("per_document_page_rate_ghs is required")
    if not errors:
        errors = _pricing.validate_pricing(tiers, rate)
    if errors:
        return {"success": False, "validation_errors": errors}
    try:
        doc = _pricing.save_pricing(tiers, rate, updated_by=operator)
    except _pricing.PricingValidationError as exc:
        return {"success": False, "validation_errors": exc.errors}
    except OSError as exc:
        return {"success": False, "error": f"could not persist pricing: {exc}"}

    _log_admin(operator, "pricing_update", {
        "tiers": sorted(doc.get("tiers", {}).keys()),
        "per_document_page_rate_ghs": doc.get("per_document_page_rate_ghs"),
    })
    return {
        "success": True,
        "tiers": doc.get("tiers"),
        "per_document_page_rate_ghs": doc.get("per_document_page_rate_ghs"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


# ── checkout (Paystack / Hubtel) + activation ─────────────────────────────

@router.post("/api/juris-kai/cc/checkout")
def cc_checkout(body: dict = Body(...),
                operator: str = Depends(require_juris_write),
                request: Request = None):
    """Initialize a subscription checkout for an account + tier."""
    from core.juris_kai import paystack_checkout as checkout
    from core.juris_kai.accounts import get_account_manager

    _rate_limit(request, operator)
    account_id = str(body.get("account_id") or "").strip()
    tier = str(body.get("tier") or "").strip()
    if not account_id or not tier:
        return {"success": False, "error": "account_id and tier are required"}

    account = get_account_manager().get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        result = checkout.create_checkout(
            account, tier, email=body.get("email"),
            callback_url=body.get("callback_url"),
            provider=body.get("provider"))
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    except PaymentConfigError as exc:
        return {"success": False, "error": f"payments not configured: {exc}"}
    except PaystackError as exc:
        logger.warning("juris checkout failed: %s", exc)
        return {"success": False, "error": str(exc)}

    _log_admin(operator, "checkout_initialize", {
        "account_id": account_id, "tier": tier,
        "provider": result.get("provider"), "mode": result.get("mode"),
    })
    return {"success": bool(result.get("success")), "checkout": result,
            **({"error": result["error"]} if result.get("error") else {})}


@router.post("/api/juris-kai/cc/checkout/verify/{reference}")
def cc_checkout_verify(reference: str,
                       operator: str = Depends(require_juris_write),
                       request: Request = None):
    """Verify a reference with Paystack then activate the tier (idempotent)."""
    from core.juris_kai import paystack_checkout as checkout

    _rate_limit(request, operator)
    try:
        verified = checkout.get_paystack_provider().verify(reference)
    except PaymentConfigError as exc:
        return {"success": False, "error": f"payments not configured: {exc}"}
    except PaystackError as exc:
        return {"success": False, "error": str(exc)}

    activation = checkout.activate_reference(reference, status=verified.get("status"))
    _log_admin(operator, "checkout_verify",
               {"reference": reference, "activated": activation.get("activated")})
    return {"success": True, "verified": verified, "activation": activation}


@router.post("/api/juris-kai/paystack/webhook")
@router.post("/api/juris-kai/cc/paystack/webhook")
async def juris_paystack_webhook(request: Request):
    """Receive a Paystack event. Authenticated solely by the HMAC signature."""
    from core.juris_kai import paystack_checkout as checkout

    signature = request.headers.get("x-paystack-signature")
    raw_body = await request.body()
    try:
        result = checkout.handle_webhook(raw_body, signature)
    except PaymentConfigError as exc:
        raise HTTPException(status_code=503, detail=f"payments not configured: {exc}")
    except PaystackError as exc:
        logger.warning("juris paystack webhook rejected: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc))
    return {"status": "ok", **result}


# ── streaming test query (SSE, local-only) ────────────────────────────────

def _sse(event: str, data: dict) -> str:
    """Format one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/api/juris-kai/cc/test-query-stream")
def cc_test_query_stream(body: dict = Body(...),
                         operator: str = Depends(require_juris_write),
                         request: Request = None):
    """Stream a local-only answer as SSE so the browser paints TTFT fast.

    Events:
      * ``token`` — ``{"text": "..."}`` for each incremental chunk
      * ``error`` — ``{"error": "..."}`` if the local stream fails
      * ``done``  — ``{"model","ttft_ms","total_ms","chars"}`` (always last)

    Uses the same ``require_juris_write`` gate and rate limit as the blocking
    ``/cc/test-query`` endpoint; the Command Center keeps that endpoint as a
    fallback when streaming is unavailable.
    """
    _rate_limit(request, operator)
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    task_type = body.get("task_type") or "juris_research"

    from core.juris_kai.prompt import build_prompt
    from core.juris_kai import streaming as jstream
    from core.juris_kai.legal_context import (
        query_knowledge_base, build_context_preamble)

    prompt_type = (task_type.replace("juris_", "legal_", 1)
                   if task_type.startswith("juris_") else "legal_research")
    docs = query_knowledge_base(query)
    context = build_context_preamble(docs)
    prompt = build_prompt(prompt_type, query) + context

    def event_stream():
        started = time.time()
        ttft_ms = None
        chars = 0
        model = jstream.DEFAULT_MODEL
        gen = None
        try:
            gen = jstream.stream_chat(prompt, task_type=task_type,
                                      timeout=_STREAM_TIMEOUT_S)
            for piece in gen:
                if not piece:
                    continue
                if ttft_ms is None:
                    ttft_ms = round((time.time() - started) * 1000, 1)
                chars += len(piece)
                yield _sse("token", {"text": piece})
                if time.time() - started > _STREAM_TIMEOUT_S:
                    yield _sse("error", {"error": "stream timeout"})
                    break
        except Exception as exc:  # transport/parse failure → report, don't 500
            yield _sse("error", {"error": str(exc)})
        finally:
            # Promptly close the upstream Ollama response on client disconnect.
            if gen is not None:
                close = getattr(gen, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
        yield _sse("done", {
            "model": model,
            "ttft_ms": ttft_ms,
            "total_ms": round((time.time() - started) * 1000, 1),
            "chars": chars,
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
