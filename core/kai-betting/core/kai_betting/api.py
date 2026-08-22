"""Kai Betting — FastAPI Backend.

Mounted on the existing Kai API at /api/betting/*.
Provides REST endpoints for predictions, odds groups, subscriptions, and admin.
"""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends, Header, Request
from pydantic import BaseModel
import bcrypt

from core.kai_betting.db import get_db, init_db, event_is_started
from core.kai_betting.models import (
    UserCreate, UserLogin, TelegramLink,
    PredictionRequest, BatchPredictionRequest, OddsGroupRequest,
    SubscriptionPurchase, PaymentCallback, PredictionSettle,
    UserPreferencesUpdate, PerformanceQuery, ConfigUpdate,
    UserResponse, SportResponse, LeagueResponse, TeamResponse,
    EventResponse, PredictionResponse, OddsGroupResponse,
    SubscriptionPlanResponse, SubscriptionResponse, PaymentResponse,
    PerformanceResponse, DashboardSummary, APIResponse,
    PredictionStatus, SubscriptionStatus, PaymentStatus,
)
from core.kai_betting.prediction_engine import PredictionEngine
from core.kai_betting.odds_engine import OddsEngine
from core.kai_betting.payments import BettingPaymentClient
from core.kai_betting.subscriptions import SubscriptionManager
from core.kai_betting.performance import PerformanceTracker
from core.kai_betting.security import rate_limit, _auth_limiter
from core.kai_betting.sessions import create_session, resolve_session, delete_session

# ── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/betting", tags=["Kai Betting"])

# Singleton engines (lazy init)
_prediction_engine: Optional[PredictionEngine] = None
_odds_engine: Optional[OddsEngine] = None
_payment_client: Optional[BettingPaymentClient] = None
_subscription_mgr: Optional[SubscriptionManager] = None
_perf_tracker: Optional[PerformanceTracker] = None


def get_prediction_engine() -> PredictionEngine:
    global _prediction_engine
    if _prediction_engine is None:
        _prediction_engine = PredictionEngine()
    return _prediction_engine


def get_odds_engine() -> OddsEngine:
    global _odds_engine
    if _odds_engine is None:
        _odds_engine = OddsEngine()
    return _odds_engine


def get_payment_client() -> BettingPaymentClient:
    global _payment_client
    if _payment_client is None:
        _payment_client = BettingPaymentClient()
    return _payment_client


def get_subscription_mgr() -> SubscriptionManager:
    global _subscription_mgr
    if _subscription_mgr is None:
        _subscription_mgr = SubscriptionManager()
    return _subscription_mgr


def get_perf_tracker() -> PerformanceTracker:
    global _perf_tracker
    if _perf_tracker is None:
        _perf_tracker = PerformanceTracker()
    return _perf_tracker


def _strip_secrets(row: dict) -> dict:
    """Remove sensitive columns (password_hash, etc.) before returning a user row."""
    return {k: v for k, v in row.items() if k not in ("password_hash",)}


async def require_webhook_secret(x_webhook_secret: Optional[str] = Header(default=None)):
    """Gate the payment callback behind a shared secret set by the payment provider config.

    Fails closed: if BETTING_WEBHOOK_SECRET isn't configured, the callback is
    unreachable rather than accepting forged payment confirmations.
    """
    expected = os.environ.get("BETTING_WEBHOOK_SECRET", "")
    if not expected or not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid webhook secret")


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    """Return the raw token from an `Authorization: Bearer <token>` header, or None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    return token or None


async def require_session(authorization: Optional[str] = Header(default=None)) -> dict:
    """Resolve Authorization: Bearer <token> to a user row. 401 if missing/invalid/expired."""
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid session token")
    with get_db() as db:
        user = resolve_session(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Missing or invalid session token")
    return _strip_secrets(dict(user))


async def require_admin_session(user: dict = Depends(require_session)) -> dict:
    """require_session, plus is_admin=1. 403 if the session is valid but not admin."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _require_owner_or_admin(path_user_id: int, session_user: dict) -> None:
    """Raise 403 unless the session belongs to path_user_id or an admin."""
    if session_user["id"] != path_user_id and not session_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Not authorized for this user")


# ── Health ───────────────────────────────────────────────────────────────────

@router.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        init_db()
        return APIResponse(success=True, data={"status": "healthy", "db": "connected"})
    except Exception as e:
        return APIResponse(success=False, error=str(e))


# ── Auth ─────────────────────────────────────────────────────────────────────

@router.post("/auth/register", response_model=APIResponse)
async def register_user(req: UserLogin):
    """Register a new user."""
    with get_db() as db:
        existing = db.execute("SELECT id FROM users WHERE email = ?", (req.email,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

        password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
        cursor = db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (req.email, password_hash)
        )
        db.commit()
        return APIResponse(success=True, data={"user_id": cursor.lastrowid})


@router.post("/auth/login", response_model=APIResponse)
@rate_limit(max_requests=20, window_seconds=300, error_message="Too many login attempts. Please wait.", limiter=_auth_limiter)
async def login_user(req: UserLogin, request: Request):
    """Login, verify credentials, and issue a session token."""
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1",
            (req.email,)
        ).fetchone()
        if not user or not bcrypt.checkpw(req.password.encode(), user["password_hash"].encode()):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        db.execute(
            "UPDATE users SET last_login_at = datetime('now') WHERE id = ?",
            (user["id"],)
        )
        db.commit()

        token = create_session(db, user["id"])

        return APIResponse(success=True, data={"user": _strip_secrets(dict(user)), "token": token})


@router.post("/auth/logout", response_model=APIResponse)
async def logout_user(authorization: Optional[str] = Header(default=None)):
    """Invalidate the current session. No-op if the token is already missing/invalid."""
    token = _extract_bearer_token(authorization)
    if token:
        with get_db() as db:
            delete_session(db, token)
    return APIResponse(success=True, data={"logged_out": True})


# ── Sports ───────────────────────────────────────────────────────────────────

@router.get("/sports", response_model=APIResponse)
async def list_sports():
    """List all active sports."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM sports WHERE is_active = 1 ORDER BY sort_order"
        ).fetchall()
        return APIResponse(success=True, data=[dict(r) for r in rows])


@router.get("/sports/{sport_key}/leagues", response_model=APIResponse)
async def list_leagues(sport_key: str):
    """List leagues for a sport."""
    with get_db() as db:
        sport = db.execute("SELECT id FROM sports WHERE key = ?", (sport_key,)).fetchone()
        if not sport:
            raise HTTPException(status_code=404, detail="Sport not found")

        rows = db.execute(
            "SELECT l.*, s.key as sport_key FROM leagues l "
            "JOIN sports s ON s.id = l.sport_id "
            "WHERE l.sport_id = ? AND l.is_active = 1 ORDER BY l.tier",
            (sport["id"],)
        ).fetchall()
        return APIResponse(success=True, data=[dict(r) for r in rows])


@router.get("/sports/{sport_key}/teams", response_model=APIResponse)
async def list_teams(
    sport_key: str,
    league_key: Optional[str] = None,
    search: Optional[str] = None
):
    """List teams, optionally filtered by league or search."""
    with get_db() as db:
        sport = db.execute("SELECT id FROM sports WHERE key = ?", (sport_key,)).fetchone()
        if not sport:
            raise HTTPException(status_code=404, detail="Sport not found")

        query = "SELECT t.*, s.key as sport_key FROM teams t JOIN sports s ON s.id = t.sport_id WHERE t.sport_id = ?"
        params: list = [sport["id"]]

        if league_key:
            query += " AND t.league_id = (SELECT id FROM leagues WHERE key = ? AND sport_id = ?)"
            params.extend([league_key, sport["id"]])

        if search:
            query += " AND (t.name LIKE ? OR t.key LIKE ? OR t.short_name LIKE ?)"
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern])

        query += " ORDER BY t.name"
        rows = db.execute(query, params).fetchall()
        return APIResponse(success=True, data=[dict(r) for r in rows])


# ── Events ───────────────────────────────────────────────────────────────────

@router.get("/events", response_model=APIResponse)
async def list_events(
    sport_key: Optional[str] = None,
    status: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    """List events with optional filters."""
    with get_db() as db:
        query = """
            SELECT e.*, s.key as sport_key, l.key as league_key,
                   ht.name as home_team, at.name as away_team
            FROM events e
            JOIN sports s ON s.id = e.sport_id
            LEFT JOIN leagues l ON l.id = e.league_id
            JOIN teams ht ON ht.id = e.home_team_id
            JOIN teams at ON at.id = e.away_team_id
            WHERE 1=1
        """
        params: list = []

        if sport_key:
            query += " AND s.key = ?"
            params.append(sport_key)
        if status:
            query += " AND e.status = ?"
            params.append(status)
        if date:
            query += " AND date(e.event_time) = date(?)"
            params.append(date)

        query += " ORDER BY e.event_time ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = db.execute(query, params).fetchall()
        return APIResponse(success=True, data=[dict(r) for r in rows])


# ── Predictions ──────────────────────────────────────────────────────────────

@router.post("/predictions/generate", response_model=APIResponse)
async def generate_prediction(req: PredictionRequest):
    """Generate a single prediction."""
    engine = get_prediction_engine()
    result = engine.predict(
        sport_key=req.sport_key,
        market_type=req.market_type.value,
        home_team=req.home_team,
        away_team=req.away_team,
        event_external_id=req.event_external_id,
        bookmaker_odds=req.bookmaker_odds,
    )

    # Save to DB
    with get_db() as db:
        # Upsert sport
        sport = db.execute("SELECT id FROM sports WHERE key = ?", (req.sport_key,)).fetchone()
        if not sport:
            raise HTTPException(status_code=404, detail=f"Sport '{req.sport_key}' not found")

        # Save prediction
        cursor = db.execute("""
            INSERT INTO predictions (
                sport_id, market_type, market_name, selection,
                bookmaker_odds, estimated_probability, implied_probability, edge,
                confidence, risk_score, data_quality, reasoning, tags,
                model_version, status, data_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'))
        """, (
            sport["id"],
            result.market_type,
            result.market_name,
            result.selection,
            result.bookmaker_odds,
            result.estimated_probability,
            result.implied_probability,
            result.edge,
            result.confidence,
            result.risk_score,
            result.data_quality,
            result.reasoning,
            ",".join(result.tags),
            result.model_version,
        ))
        db.commit()
        result_db = db.execute(
            "SELECT p.*, s.key as sport_key FROM predictions p JOIN sports s ON s.id = p.sport_id WHERE p.id = ?",
            (cursor.lastrowid,)
        ).fetchone()

    return APIResponse(success=True, data=dict(result_db))


@router.post("/predictions/batch", response_model=APIResponse)
async def generate_batch_predictions(req: BatchPredictionRequest):
    """Generate multiple predictions at once."""
    engine = get_prediction_engine()
    results = []
    for pred_req in req.predictions:
        result = engine.predict(
            sport_key=pred_req.sport_key,
            market_type=pred_req.market_type.value,
            home_team=pred_req.home_team,
            away_team=pred_req.away_team,
            bookmaker_odds=pred_req.bookmaker_odds,
        )
        results.append(result)

    return APIResponse(
        success=True,
        data={"count": len(results), "predictions": [r.__dict__ for r in results]}
    )


@router.get("/predictions", response_model=APIResponse)
async def list_predictions(
    status: Optional[str] = None,
    sport_key: Optional[str] = None,
    market_type: Optional[str] = None,
    show_started: bool = False,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    """List predictions with filters.

    By default, predictions whose game has already started are hidden,
    so users only see upcoming picks. Pass show_started=true to see all.
    """
    with get_db() as db:
        query = """
            SELECT p.*, s.key as sport_key, l.key as league_key, l.name as league_name,
                   e.event_time, e.status as event_status,
                   ht.name as home_team, at.name as away_team
            FROM predictions p
            JOIN sports s ON s.id = p.sport_id
            LEFT JOIN events e ON e.id = p.event_id
            LEFT JOIN leagues l ON l.id = e.league_id
            LEFT JOIN teams ht ON ht.id = e.home_team_id
            LEFT JOIN teams at ON at.id = e.away_team_id
            WHERE 1=1
        """
        params: list = []

        if status:
            query += " AND p.status = ?"
            params.append(status)
        if sport_key:
            query += " AND s.key = ?"
            params.append(sport_key)
        if market_type:
            query += " AND p.market_type = ?"
            params.append(market_type)

        query += " ORDER BY e.event_time ASC, p.created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = db.execute(query, params).fetchall()
        data = []
        for r in rows:
            d = dict(r)
            if not show_started and event_is_started(d.get("event_time")):
                continue
            data.append(d)
        return APIResponse(success=True, data=data)


@router.get("/predictions/{prediction_id}", response_model=APIResponse)
async def get_prediction(prediction_id: int):
    """Get a single prediction by ID."""
    with get_db() as db:
        row = db.execute(
            """SELECT p.*, s.key as sport_key, l.key as league_key, l.name as league_name,
                      e.event_time, e.status as event_status,
                      ht.name as home_team, at.name as away_team
               FROM predictions p
               JOIN sports s ON s.id = p.sport_id
               LEFT JOIN events e ON e.id = p.event_id
               LEFT JOIN leagues l ON l.id = e.league_id
               LEFT JOIN teams ht ON ht.id = e.home_team_id
               LEFT JOIN teams at ON at.id = e.away_team_id
               WHERE p.id = ?""",
            (prediction_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Prediction not found")

        # Get outcome if settled
        outcome = db.execute(
            "SELECT * FROM prediction_results WHERE prediction_id = ?",
            (prediction_id,)
        ).fetchone()

        data = dict(row)
        if outcome:
            data["outcome"] = outcome["outcome"]
        return APIResponse(success=True, data=data)


@router.post("/predictions/{prediction_id}/settle", response_model=APIResponse)
async def settle_prediction(prediction_id: int, req: PredictionSettle):
    """Settle a prediction with its result."""
    with get_db() as db:
        pred = db.execute(
            "SELECT * FROM predictions WHERE id = ?", (prediction_id,)
        ).fetchone()
        if not pred:
            raise HTTPException(status_code=404, detail="Prediction not found")

        db.execute(
            "INSERT OR REPLACE INTO prediction_results "
            "(prediction_id, outcome, actual_score_home, actual_score_away, settled_by, settled_at, notes) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'), ?)",
            (prediction_id, req.outcome.value, req.actual_score_home,
             req.actual_score_away, req.settled_by, req.notes)
        )

        new_status = req.outcome.value
        db.execute(
            "UPDATE predictions SET status = ?, settled_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
            (new_status, prediction_id)
        )
        db.commit()

    return APIResponse(success=True, data={"prediction_id": prediction_id, "outcome": req.outcome.value})


# ── Odds Groups ──────────────────────────────────────────────────────────────

@router.post("/odds-groups/generate", response_model=APIResponse)
async def generate_odds_group(req: OddsGroupRequest):
    """Generate an odds group (accumulator)."""
    engine = get_odds_engine()
    result = engine.generate(
        target_odds=req.target_odds,
        risk_level=req.risk_level.value,
        sport_keys=req.sport_keys if req.sport_keys else None,
        market_types=[m.value for m in req.market_types] if req.market_types else None,
        min_selections=req.min_selections,
        max_selections=req.max_selections,
    )

    # Save to DB
    with get_db() as db:
        cursor = db.execute("""
            INSERT INTO odds_groups (
                target_odds, label, risk_level, combined_odds,
                estimated_probability, average_confidence, num_selections, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        """, (
            result.target_odds,
            result.label,
            result.risk_level,
            result.combined_odds,
            result.estimated_probability,
            result.average_confidence,
            len(result.selections),
        ))
        group_id = cursor.lastrowid

        for i, sel in enumerate(result.selections):
            # Look up prediction by match
            pred_id = _ensure_prediction_saved(db, sel)
            db.execute(
                "INSERT OR IGNORE INTO odds_group_selections (odds_group_id, prediction_id, sort_order) VALUES (?, ?, ?)",
                (group_id, pred_id, i + 1)
            )

        db.commit()

        group = db.execute("SELECT * FROM odds_groups WHERE id = ?", (group_id,)).fetchone()
        return APIResponse(success=True, data=dict(group))


@router.get("/odds-groups", response_model=APIResponse)
async def list_odds_groups(
    status: str = "active",
    limit: int = Query(default=20, le=100),
    offset: int = 0,
):
    """List odds groups, each with its full selection details."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM odds_groups WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status, limit, offset)
        ).fetchall()
        data = []
        for g in rows:
            gd = dict(g)
            gd["selections"] = _get_group_selections(db, g["id"])
            data.append(gd)
        return APIResponse(success=True, data=data)


@router.get("/odds-groups/{group_id}", response_model=APIResponse)
async def get_odds_group(group_id: int):
    """Get an odds group with its selections."""
    with get_db() as db:
        group = db.execute("SELECT * FROM odds_groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="Odds group not found")

        data = dict(group)
        data["selections"] = _get_group_selections(db, group_id)
        return APIResponse(success=True, data=data)


# ── Subscriptions ────────────────────────────────────────────────────────────

@router.get("/plans", response_model=APIResponse)
async def list_plans():
    """List available subscription plans."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM subscription_plans WHERE is_active = 1 ORDER BY duration_days"
        ).fetchall()
        return APIResponse(success=True, data=[dict(r) for r in rows])


@router.post("/subscriptions/purchase", response_model=APIResponse)
async def purchase_subscription(user_id: int, req: SubscriptionPurchase, user: dict = Depends(require_session)):
    """Initiate a subscription purchase."""
    _require_owner_or_admin(user_id, user)
    mgr = get_subscription_mgr()
    result = mgr.purchase(
        user_id=user_id,
        plan_key=req.plan_key,
        payment_provider=req.payment_provider,
        payment_method=req.payment_method,
        phone_number=req.phone_number,
        currency=req.currency,
    )
    return APIResponse(success=result.get("success", False), data=result)


@router.get("/subscriptions/{user_id}", response_model=APIResponse)
async def get_user_subscription(user_id: int, user: dict = Depends(require_session)):
    """Get a user's current subscription."""
    _require_owner_or_admin(user_id, user)
    with get_db() as db:
        sub = db.execute("""
            SELECT s.*, sp.key as plan_key, sp.name as plan_name
            FROM subscriptions s
            JOIN subscription_plans sp ON sp.id = s.plan_id
            WHERE s.user_id = ? AND s.status = 'active'
            ORDER BY s.created_at DESC LIMIT 1
        """, (user_id,)).fetchone()

        if not sub:
            return APIResponse(success=True, data={"has_subscription": False})

        return APIResponse(success=True, data=dict(sub))


# ── Payments ─────────────────────────────────────────────────────────────────

@router.post("/payments/callback", response_model=APIResponse, dependencies=[Depends(require_webhook_secret)])
async def payment_callback(req: PaymentCallback):
    """Handle payment provider callback."""
    client = get_payment_client()
    result = client.handle_callback(
        transaction_id=req.transaction_id,
        status=req.status,
        provider=req.provider,
        amount=req.amount,
        currency=req.currency,
        provider_response=req.provider_response,
    )
    return APIResponse(success=True, data=result)


@router.get("/payments/{user_id}", response_model=APIResponse)
async def list_user_payments(
    user_id: int,
    limit: int = Query(default=20, le=100),
    user: dict = Depends(require_session),
):
    """List a user's payments."""
    _require_owner_or_admin(user_id, user)
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return APIResponse(success=True, data=[dict(r) for r in rows])


# ── Performance ──────────────────────────────────────────────────────────────

@router.get("/performance", response_model=APIResponse)
async def get_performance(
    period: str = Query(default="all_time"),
    sport_key: Optional[str] = None,
    market_type: Optional[str] = None,
    days: int = Query(default=30, le=365),
):
    """Get performance metrics."""
    tracker = get_perf_tracker()
    metrics = tracker.get_metrics(
        period=period,
        sport_key=sport_key,
        market_type=market_type,
        days=days,
    )
    return APIResponse(success=True, data=metrics)


@router.get("/dashboard", response_model=APIResponse)
async def get_dashboard():
    """Get dashboard summary."""
    tracker = get_perf_tracker()
    summary = tracker.get_dashboard_summary()
    return APIResponse(success=True, data=summary)


# ── Admin ────────────────────────────────────────────────────────────────────

@router.get("/admin/config", response_model=APIResponse, dependencies=[Depends(require_admin_session)])
async def get_config():
    """Get all config values (admin only)."""
    with get_db() as db:
        rows = db.execute("SELECT key, value, description, updated_at FROM betting_config").fetchall()
        return APIResponse(success=True, data={r["key"]: dict(r) for r in rows})


@router.put("/admin/config", response_model=APIResponse, dependencies=[Depends(require_admin_session)])
async def update_config(req: ConfigUpdate):
    """Update a config value (admin only)."""
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO betting_config (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (req.key, req.value)
        )
        db.commit()
    return APIResponse(success=True, data={"key": req.key, "value": req.value})


@router.get("/admin/users", response_model=APIResponse, dependencies=[Depends(require_admin_session)])
async def admin_list_users(
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
):
    """List all users (admin only)."""
    with get_db() as db:
        query = """
            SELECT u.*,
                   s.status as subscription_status,
                   s.expires_at as subscription_expires
            FROM users u
            LEFT JOIN subscriptions s ON s.user_id = u.id AND s.status = 'active'
            WHERE 1=1
        """
        params: list = []

        if search:
            query += " AND (u.email LIKE ? OR u.full_name LIKE ? OR u.phone_number LIKE ?)"
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern])
        if is_active is not None:
            query += " AND u.is_active = ?"
            params.append(1 if is_active else 0)

        query += " ORDER BY u.created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = db.execute(query, params).fetchall()
        return APIResponse(success=True, data=[_strip_secrets(dict(r)) for r in rows])


@router.get("/admin/audit", response_model=APIResponse, dependencies=[Depends(require_admin_session)])
async def admin_audit_logs(
    entity_type: Optional[str] = None,
    limit: int = Query(default=100, le=1000),
    offset: int = 0,
):
    """Get audit logs (admin only)."""
    with get_db() as db:
        query = "SELECT * FROM audit_logs WHERE 1=1"
        params: list = []

        if entity_type:
            query += " AND entity_type = ?"
            params.append(entity_type)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = db.execute(query, params).fetchall()
        return APIResponse(success=True, data=[dict(r) for r in rows])


class AdminUserUpdate(BaseModel):
    is_admin: Optional[bool] = None
    subscription_status: Optional[str] = None  # 'active', 'cancelled', or None
    subscription_expires: Optional[str] = None


@router.put("/admin/users/{user_id}", response_model=APIResponse, dependencies=[Depends(require_admin_session)])
async def admin_update_user(user_id: int, update: AdminUserUpdate):
    """Update user admin status or subscription (admin only)."""
    with get_db() as db:
        user = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")

        if update.is_admin is not None:
            db.execute("UPDATE users SET is_admin = ?, updated_at = datetime('now') WHERE id = ?",
                       (1 if update.is_admin else 0, user_id))
        if update.subscription_status is not None:
            expires = update.subscription_expires or (
                "datetime('now', '+30 days')" if update.subscription_status == "active" else None
            )
            db.execute("""UPDATE users SET subscription_status = ?, subscription_expires = ?,
                          updated_at = datetime('now') WHERE id = ?""",
                       (update.subscription_status, expires, user_id))

        db.commit()

        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return APIResponse(success=True, data=_strip_secrets(dict(user)))


# ── User Preferences ─────────────────────────────────────────────────────────

@router.get("/preferences/{user_id}", response_model=APIResponse)
async def get_preferences(user_id: int, user: dict = Depends(require_session)):
    """Get user notification preferences."""
    _require_owner_or_admin(user_id, user)
    with get_db() as db:
        prefs = db.execute(
            "SELECT * FROM user_preferences WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not prefs:
            # Create defaults
            db.execute(
                "INSERT INTO user_preferences (user_id) VALUES (?)", (user_id,)
            )
            db.commit()
            prefs = db.execute(
                "SELECT * FROM user_preferences WHERE user_id = ?", (user_id,)
            ).fetchone()
        return APIResponse(success=True, data=dict(prefs))


@router.put("/preferences/{user_id}", response_model=APIResponse)
async def update_preferences(user_id: int, req: UserPreferencesUpdate, user: dict = Depends(require_session)):
    """Update user notification preferences."""
    _require_owner_or_admin(user_id, user)
    with get_db() as db:
        db.execute("""
            INSERT OR REPLACE INTO user_preferences (
                user_id, selected_sports, selected_markets,
                notification_picks, notification_results,
                notification_odds_groups, notification_daily_summary,
                telegram_notifications, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            user_id,
            req.selected_sports,
            req.selected_markets,
            int(req.notification_picks),
            int(req.notification_results),
            int(req.notification_odds_groups),
            int(req.notification_daily_summary),
            int(req.telegram_notifications),
        ))
        db.commit()
    return APIResponse(success=True, data={"user_id": user_id})


# ── Helper ───────────────────────────────────────────────────────────────────

def _get_group_selections(db, group_id: int) -> list:
    """Return a group's selections with full team/event/league context."""
    selections = db.execute("""
        SELECT p.*, s.key as sport_key, l.key as league_key, l.name as league_name,
               e.event_time, ht.name as home_team, at.name as away_team,
               ogs.sort_order
        FROM odds_group_selections ogs
        JOIN predictions p ON p.id = ogs.prediction_id
        JOIN sports s ON s.id = p.sport_id
        LEFT JOIN events e ON e.id = p.event_id
        LEFT JOIN leagues l ON l.id = e.league_id
        LEFT JOIN teams ht ON ht.id = e.home_team_id
        LEFT JOIN teams at ON at.id = e.away_team_id
        WHERE ogs.odds_group_id = ?
        ORDER BY ogs.sort_order
    """, (group_id,)).fetchall()
    return [dict(s) for s in selections]


def _ensure_prediction_saved(db, result) -> int:
    """Save a prediction result to DB if not already saved, return its ID.

    Odds-group candidates come from OddsEngine._get_candidates(), which reads
    existing rows out of the predictions table (already linked to their
    event via event_id). If `result` carries a source_prediction_id, reuse
    that row instead of inserting a copy — a fresh INSERT here never sets
    event_id/league_id, which orphans the copy from the events table and
    permanently blocks it from ever being settled, and repeated group
    regeneration would otherwise re-duplicate the same prediction forever.
    """
    if result.source_prediction_id is not None:
        existing = db.execute(
            "SELECT id FROM predictions WHERE id = ?", (result.source_prediction_id,)
        ).fetchone()
        if existing:
            return existing["id"]

    sport = db.execute("SELECT id FROM sports WHERE key = ?", (result.sport_key,)).fetchone()
    if not sport:
        sport_id = db.execute(
            "INSERT INTO sports (key, name) VALUES (?, ?)",
            (result.sport_key, result.sport_key)
        ).lastrowid
    else:
        sport_id = sport["id"]

    cursor = db.execute("""
        INSERT INTO predictions (
            sport_id, market_type, market_name, selection,
            bookmaker_odds, estimated_probability, implied_probability, edge,
            confidence, risk_score, data_quality, reasoning, tags,
            model_version, status, data_timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'))
    """, (
        sport_id,
        result.market_type,
        result.market_name,
        result.selection,
        result.bookmaker_odds,
        result.estimated_probability,
        result.implied_probability,
        result.edge,
        result.confidence,
        result.risk_score,
        result.data_quality,
        result.reasoning,
        ",".join(result.tags),
        result.model_version,
    ))
    return cursor.lastrowid
