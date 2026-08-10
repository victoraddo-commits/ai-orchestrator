"""Kai Betting — Data Models.

Pydantic models for request/response validation and dataclasses for internal state.
Uses the same patterns as the rest of Kai infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field, EmailStr


# ── Enums ────────────────────────────────────────────────────────────────────

class EventStatus(str, Enum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


class PredictionStatus(str, Enum):
    PENDING = "pending"
    QUALITY_CHECK = "quality_check"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    WON = "won"
    LOST = "lost"
    PUSH = "push"
    VOID = "void"
    CANCELLED = "cancelled"


class PredictionOutcome(str, Enum):
    WON = "won"
    LOST = "lost"
    PUSH = "push"
    VOID = "void"


class SubscriptionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class RiskLevel(str, Enum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    HIGH_RISK = "high_risk"


class MarketType(str, Enum):
    MATCH_RESULT = "match_result"
    DOUBLE_CHANCE = "double_chance"
    DRAW_NO_BET = "draw_no_bet"
    OVER_UNDER = "over_under"
    BTTS = "btts"
    TEAM_GOALS = "team_goals"
    HT_RESULT = "ht_result"
    HT_GOALS = "ht_goals"
    HANDICAP = "handicap"
    SET_BETTING = "set_betting"


# ── Pydantic Request Models ─────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    full_name: str = ""
    country: str = ""
    phone_number: str = ""


class UserLogin(BaseModel):
    email: str
    password: str


class TelegramLink(BaseModel):
    telegram_id: str
    username: str = ""
    first_name: str = ""
    chat_id: str


class PredictionRequest(BaseModel):
    sport_key: str
    market_type: MarketType
    event_external_id: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    event_time: Optional[str] = None
    bookmaker_odds: Optional[float] = None
    target_confidence: float = Field(default=50.0, ge=0, le=100)


class BatchPredictionRequest(BaseModel):
    predictions: List[PredictionRequest] = Field(..., min_length=1, max_length=100)


class OddsGroupRequest(BaseModel):
    target_odds: float = Field(gt=1.0)
    risk_level: RiskLevel = RiskLevel.MODERATE
    sport_keys: List[str] = Field(default_factory=list)
    market_types: List[MarketType] = Field(default_factory=list)
    min_selections: int = Field(default=2, ge=1, le=50)
    max_selections: int = Field(default=8, ge=2, le=50)
    include_reasoning: bool = False


class SubscriptionPurchase(BaseModel):
    plan_key: str
    payment_provider: str = "hubtel"
    payment_method: str = "mobile_money"
    phone_number: str = ""
    currency: str = "GHS"


class PaymentCallback(BaseModel):
    transaction_id: str
    status: str
    provider: str
    amount: float
    currency: str = "GHS"
    provider_response: Dict[str, Any] = Field(default_factory=dict)


class PredictionSettle(BaseModel):
    prediction_id: int
    outcome: PredictionOutcome
    actual_score_home: Optional[int] = None
    actual_score_away: Optional[int] = None
    settled_by: str = "manual"
    notes: str = ""


class UserPreferencesUpdate(BaseModel):
    selected_sports: str = ""
    selected_markets: str = ""
    notification_picks: bool = True
    notification_results: bool = True
    notification_odds_groups: bool = False
    notification_daily_summary: bool = False
    telegram_notifications: bool = True


class PerformanceQuery(BaseModel):
    period: str = "all_time"  # 'daily', 'weekly', 'monthly', 'all_time'
    sport_key: Optional[str] = None
    market_type: Optional[MarketType] = None
    model_id: Optional[int] = None
    days: int = Field(default=30, ge=1, le=365)


class ConfigUpdate(BaseModel):
    key: str
    value: str


# ── Pydantic Response Models ─────────────────────────────────────────────────

class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    country: str
    phone_number: str
    is_active: bool
    is_admin: bool
    created_at: str
    last_login_at: Optional[str] = None
    subscription_status: Optional[str] = None
    subscription_expires: Optional[str] = None


class SportResponse(BaseModel):
    id: int
    key: str
    name: str
    icon: str
    is_active: bool
    sort_order: int


class LeagueResponse(BaseModel):
    id: int
    sport_key: str
    key: str
    name: str
    country: str
    tier: int


class TeamResponse(BaseModel):
    id: int
    sport_key: str
    key: str
    name: str
    short_name: str
    country: str


class EventResponse(BaseModel):
    id: int
    external_id: Optional[str]
    sport_key: str
    league_key: Optional[str]
    home_team: str
    away_team: str
    event_time: str
    status: str
    home_score: Optional[int]
    away_score: Optional[int]
    venue: str
    round: str
    season: str


class PredictionResponse(BaseModel):
    id: int
    event_id: int
    sport_key: str
    league_key: Optional[str]
    market_type: str
    market_name: str
    selection: str
    bookmaker_odds: Optional[float]
    estimated_probability: float
    edge: Optional[float]
    confidence: float
    risk_score: float
    data_quality: float
    reasoning: str
    tags: str
    status: str
    published_at: Optional[str]
    created_at: str
    outcome: Optional[str] = None


class OddsGroupResponse(BaseModel):
    id: int
    target_odds: float
    label: str
    risk_level: str
    combined_odds: float
    estimated_probability: Optional[float]
    average_confidence: float
    num_selections: int
    status: str
    published_at: Optional[str]
    expires_at: Optional[str]
    created_at: str
    selections: List[PredictionResponse] = Field(default_factory=list)


class SubscriptionPlanResponse(BaseModel):
    id: int
    key: str
    name: str
    duration_days: int
    price: float
    currency: str
    features: Dict[str, Any]
    is_active: bool


class SubscriptionResponse(BaseModel):
    id: int
    plan_key: str
    plan_name: str
    status: str
    started_at: Optional[str]
    expires_at: Optional[str]
    auto_renew: bool
    created_at: str


class PaymentResponse(BaseModel):
    id: int
    transaction_id: Optional[str]
    provider: str
    amount: float
    currency: str
    status: str
    payment_method: str
    created_at: str


class PerformanceResponse(BaseModel):
    period: str
    period_start: str
    period_end: Optional[str]
    sport_key: Optional[str]
    market_type: Optional[str]
    total_predictions: int
    wins: int
    losses: int
    pushes: int
    voids: int
    win_rate: float
    roi: float
    average_odds: float
    average_confidence: float
    profit_loss: float


class DashboardSummary(BaseModel):
    total_users: int
    active_subscriptions: int
    total_predictions: int
    published_predictions: int
    overall_win_rate: float
    overall_roi: float
    total_revenue: float
    active_odds_groups: int
    sports_coverage: List[Dict[str, Any]]
    recent_performance: List[PerformanceResponse]


class APIResponse(BaseModel):
    success: bool
    data: Any = None
    error: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


# ── Internal Dataclasses ─────────────────────────────────────────────────────

@dataclass
class PredictionInput:
    """Raw input for the prediction engine."""
    sport_key: str
    market_type: str
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    event_external_id: Optional[str] = None
    event_time: Optional[str] = None
    bookmaker_odds: Optional[float] = None
    stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PredictionResult:
    """Output from the prediction engine."""
    sport_key: str
    league_key: Optional[str]
    market_type: str
    market_name: str
    selection: str
    estimated_probability: float
    bookmaker_odds: Optional[float] = None
    implied_probability: Optional[float] = None
    edge: Optional[float] = None
    confidence: float = 50.0
    risk_score: float = 0.0
    data_quality: float = 0.0
    reasoning: str = ""
    tags: List[str] = field(default_factory=list)
    correlation_group: str = ""
    model_version: str = "1.0.0"

    def __post_init__(self):
        if self.bookmaker_odds and self.bookmaker_odds > 0:
            self.implied_probability = 1.0 / self.bookmaker_odds
        if self.estimated_probability and self.implied_probability:
            self.edge = self.estimated_probability - self.implied_probability


@dataclass
class OddsGroupResult:
    """Output from the odds group engine."""
    target_odds: float
    label: str
    risk_level: str
    selections: List[PredictionResult]
    combined_odds: float = 1.0
    average_confidence: float = 0.0
    estimated_probability: Optional[float] = None
    status: str = "active"

    def __post_init__(self):
        if self.selections:
            self.combined_odds = 1.0
            total_conf = 0.0
            total_prob = 1.0
            for s in self.selections:
                if s.bookmaker_odds:
                    self.combined_odds *= s.bookmaker_odds
                self.average_confidence = total_conf
                total_conf += s.confidence
                total_prob *= s.estimated_probability
            self.average_confidence = total_conf / len(self.selections) if self.selections else 0.0
            self.estimated_probability = total_prob


@dataclass
class PerformanceSnapshot:
    """Aggregated performance for a period."""
    period: str
    period_start: datetime
    period_end: Optional[datetime] = None
    sport_key: Optional[str] = None
    market_type: Optional[str] = None
    total: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    voids: int = 0
    total_stake: float = 0.0
    total_return: float = 0.0

    @property
    def win_rate(self) -> float:
        settled = self.wins + self.losses + self.pushes
        return self.wins / settled if settled > 0 else 0.0

    @property
    def roi(self) -> float:
        return ((self.total_return - self.total_stake) / self.total_stake * 100) if self.total_stake > 0 else 0.0

    @property
    def profit_loss(self) -> float:
        return self.total_return - self.total_stake
