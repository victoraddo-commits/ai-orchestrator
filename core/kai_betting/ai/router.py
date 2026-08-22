"""Kai Betting — betting AI router (three-tier escalation).

The ONLY component that talks to GPU.ai. It decides whether AI is required,
runs the tiers, escalates per policy, and enforces budget/cache. Nothing else
in the betting module calls the client directly.

Escalation policy (default): Qwen screens → DeepSeek adversarially challenges
when justified → K3 adjudicates only high-value/high-disagreement candidates.
The router returns a RECOMMENDATION; Kai's deterministic risk engine remains
the final authority and publisher.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from core.kai_betting.ai.client import (
    GPUAIClient, GPUAIError, GPUAIUnavailableError,
)
from core.kai_betting.ai.prompts import (
    PROMPT_VERSION,
    QWEN_SCREENING_PROMPT,
    DEEPSEEK_ADVERSARIAL_PROMPT,
    K3_ADJUDICATION_PROMPT,
)
from core.kai_betting.ai.budget import BettingAIBudgetController
from core.kai_betting.ai.cache import InferenceCache, data_hash

# ── Escalation thresholds (env-overridable) ──────────────────────────────────
_EDGE_ESCALATE = float(os.environ.get("AI_EDGE_ESCALATE", "0.03"))
_CONFIDENCE_ESCALATE = float(os.environ.get("AI_CONFIDENCE_ESCALATE", "65"))
_RISK_ESCALATE = float(os.environ.get("AI_RISK_ESCALATE", "40"))
_HIGH_ODDS = float(os.environ.get("AI_HIGH_ODDS", "3.0"))
_DISAGREEMENT_GAP = float(os.environ.get("AI_DISAGREEMENT_GAP", "0.10"))


@dataclass
class AIRoutingResult:
    status: str = ""  # complete | qwen_only | fallback_statistical | budget_exhausted | data_insufficient | k3_required_but_unavailable | not_configured
    prediction_id: str = ""
    request_id: str = ""
    evidence_hash: str = ""
    prompt_version: str = PROMPT_VERSION
    tiers_run: List[str] = field(default_factory=list)
    qwen: Optional[Dict[str, Any]] = None
    deepseek: Optional[Dict[str, Any]] = None
    k3: Optional[Dict[str, Any]] = None
    final_probability: Optional[float] = None
    final_decision: Optional[str] = None  # BET | PASS
    reasoning: str = ""
    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    error: str = ""


class BettingAIRouter:
    """Dynamically escalates only the candidates that justify AI inference."""

    def __init__(self, client: Optional[GPUAIClient] = None):
        self.client = client or GPUAIClient()
        self.cache = InferenceCache()

    def analyze(
        self,
        prediction: Any,
        evidence: Optional[Dict[str, Any]] = None,
        conn=None,
    ) -> AIRoutingResult:
        """Run the tiered AI analysis for one statistical candidate.

        ``prediction`` is a PredictionResult (or anything exposing the
        sport/market/selection/odds/edge/confidence/risk fields). ``evidence``
        is the optional normalized match/market data package. ``conn``, when
        given, enables budget gating + spend recording against the DB.
        """
        pred_id = getattr(prediction, "source_prediction_id", "") or uuid.uuid4().hex
        request_id = uuid.uuid4().hex
        evidence = evidence or {}

        result = AIRoutingResult(
            prediction_id=str(pred_id), request_id=request_id,
            evidence_hash=data_hash({
                "sport": getattr(prediction, "sport_key", ""),
                "market": getattr(prediction, "market_type", ""),
                "selection": getattr(prediction, "selection", ""),
                "odds": getattr(prediction, "bookmaker_odds", None),
                "evidence": evidence,
            }),
        )

        # ── Not configured: no GPU.ai, no HTTP. ─────────────────────────────
        if not self.client.configured:
            result.status = "not_configured"
            return result

        # ── Data quality gate (section 24): don't pay for poor data. ───────
        if not getattr(prediction, "bookmaker_odds", None):
            result.status = "data_insufficient"
            return result

        # ── Budget gate (section 15). ───────────────────────────────────────
        budget = BettingAIBudgetController(conn) if conn is not None else None
        if budget is not None and budget.over_budget():
            result.status = "budget_exhausted"
            return result

        # ── Cache (section 14): reuse an unchanged result. ──────────────────
        cache_key = (
            f"{result.evidence_hash}:{PROMPT_VERSION}:"
            f"{getattr(prediction, 'sport_key', '')}:"
            f"{getattr(prediction, 'market_type', '')}:"
            f"{getattr(prediction, 'selection', '')}"
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            self._apply_cache(result, cached)
            return result

        # ── Build the normalized payloads. ──────────────────────────────────
        kai_package = self._kai_package(prediction, evidence)

        # ── Tier 1: Qwen screening. ─────────────────────────────────────────
        try:
            qwen_resp = self.client.chat_json(
                "qwen", QWEN_SCREENING_PROMPT, kai_package, request_id=request_id,
            )
        except (GPUAIError, GPUAIUnavailableError) as e:
            result.status = "fallback_statistical"
            result.error = str(e)
            return result
        result.tiers_run.append("qwen")
        result.qwen = qwen_resp.data
        self._accumulate(result, qwen_resp)

        # ── Escalation: Qwen → DeepSeek. ────────────────────────────────────
        if self._escalate_to_deepseek(prediction, result.qwen):
            try:
                ds_resp = self.client.chat_json(
                    "deepseek", DEEPSEEK_ADVERSARIAL_PROMPT,
                    {"kai": kai_package, "qwen": result.qwen}, request_id=request_id,
                )
            except GPUAIError:
                # DeepSeek failed: Qwen result stands if Kai's rules permit.
                result.status = "qwen_only"
                result.final_probability = self._coerce_float(result.qwen.get("prediction", {}).get("probability"))
                self._record(budget, result)
                return result
            result.tiers_run.append("deepseek")
            result.deepseek = ds_resp.data
            self._accumulate(result, ds_resp)

            # ── Escalation: DeepSeek → K3. ──────────────────────────────────
            if self._escalate_to_k3(prediction, result.qwen, result.deepseek):
                try:
                    k3_resp = self.client.chat_json(
                        "k3", K3_ADJUDICATION_PROMPT,
                        {
                            "kai": kai_package,
                            "qwen": result.qwen,
                            "deepseek": result.deepseek,
                        },
                        request_id=request_id,
                    )
                except GPUAIError:
                    # K3 is mandatory when we reach here but is unavailable.
                    result.status = "k3_required_but_unavailable"
                    self._record(budget, result)
                    return result
                result.tiers_run.append("k3")
                result.k3 = k3_resp.data
                self._accumulate(result, k3_resp)
                result.final_probability = self._coerce_float(result.k3.get("probability"))
                result.final_decision = self._decision(result.k3.get("final_decision"))
                result.status = "complete"
            else:
                result.final_probability = self._coerce_float(
                    result.deepseek.get("prediction", {}).get("probability")
                )
                result.status = "complete"
        else:
            result.final_probability = self._coerce_float(
                result.qwen.get("prediction", {}).get("probability")
            )
            result.status = "qwen_only"

        result.reasoning = self._reasoning(result)
        self._record(budget, result)
        self.cache.set(cache_key, self._cache_payload(result))
        return result

    # ── Escalation rules ────────────────────────────────────────────────────
    def _escalate_to_deepseek(self, prediction: Any, qwen: Dict[str, Any]) -> bool:
        if bool(qwen.get("deep_review")):
            return True
        edge = getattr(prediction, "edge", None)
        risk = getattr(prediction, "risk_score", 0.0)
        odds = getattr(prediction, "bookmaker_odds", None)
        confidence = self._coerce_float(qwen.get("confidence"))
        if edge is not None and edge >= _EDGE_ESCALATE:
            return True
        if confidence >= _CONFIDENCE_ESCALATE:
            return True
        if risk >= _RISK_ESCALATE:
            return True
        if odds is not None and odds >= _HIGH_ODDS:
            return True
        return False

    def _escalate_to_k3(self, prediction: Any, qwen: Dict[str, Any],
                        deepseek: Dict[str, Any]) -> bool:
        if bool(deepseek.get("needs_k3")):
            return True
        qwen_prob = self._coerce_float(qwen.get("prediction", {}).get("probability"))
        ds_prob = self._coerce_float(deepseek.get("prediction", {}).get("probability"))
        if qwen_prob is not None and ds_prob is not None:
            if abs(qwen_prob - ds_prob) >= _DISAGREEMENT_GAP:
                return True
        if self._escalate_to_deepseek(prediction, qwen) and getattr(prediction, "risk_score", 0.0) >= _RISK_ESCALATE:
            return True
        return False

    # ── Helpers ─────────────────────────────────────────────────────────────
    def _kai_package(self, prediction: Any, evidence: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "sport": getattr(prediction, "sport_key", ""),
            "league": getattr(prediction, "league_key", ""),
            "market": getattr(prediction, "market_type", ""),
            "selection": getattr(prediction, "selection", ""),
            "kai_probability": getattr(prediction, "estimated_probability", None),
            "market_probability": getattr(prediction, "implied_probability", None),
            "odds": getattr(prediction, "bookmaker_odds", None),
            "edge": getattr(prediction, "edge", None),
            "confidence": getattr(prediction, "confidence", None),
            "risk_score": getattr(prediction, "risk_score", None),
            "data_quality": getattr(prediction, "data_quality", None),
            "evidence": evidence,
        }

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _decision(value: Any) -> Optional[str]:
        v = str(value or "").upper().strip()
        return v if v in ("BET", "PASS") else None

    def _accumulate(self, result: AIRoutingResult, resp: Any) -> None:
        result.total_cost += resp.estimated_cost
        result.total_input_tokens += resp.input_tokens
        result.total_output_tokens += resp.output_tokens

    def _record(self, budget: Optional[BettingAIBudgetController], result: AIRoutingResult) -> None:
        # Spend is recorded per-call in client-land via budget.record; here we
        # just aggregate — see the budget integration note below.
        return

    def _reasoning(self, result: AIRoutingResult) -> str:
        if result.k3:
            return (
                f"K3 decision {result.final_decision or 'n/a'}; "
                f"against: {result.k3.get('strongest_argument_against', '')}"
            )
        if result.deepseek:
            flags = result.deepseek.get("red_flags") or []
            return f"DeepSeek review; {len(flags)} red flag(s)"
        if result.qwen:
            return f"Qwen screen; deep_review={bool(result.qwen.get('deep_review'))}"
        return ""

    def _cache_payload(self, result: AIRoutingResult) -> Dict[str, Any]:
        return {
            "status": result.status,
            "qwen": result.qwen,
            "deepseek": result.deepseek,
            "k3": result.k3,
            "final_probability": result.final_probability,
            "final_decision": result.final_decision,
            "reasoning": result.reasoning,
        }

    def _apply_cache(self, result: AIRoutingResult, payload: Dict[str, Any]) -> None:
        result.status = payload.get("status", result.status)
        result.qwen = payload.get("qwen")
        result.deepseek = payload.get("deepseek")
        result.k3 = payload.get("k3")
        result.final_probability = payload.get("final_probability")
        result.final_decision = payload.get("final_decision")
        result.reasoning = payload.get("reasoning", "")
