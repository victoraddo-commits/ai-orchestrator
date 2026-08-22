"""Kai Betting — Automation Workers (Phase 16).

Scheduled tasks that run on the Kai orchestrator cycle:
  - Daily prediction generation from event data
  - Auto-settlement of finished matches
  - Subscription expiry checks
  - Odds group refresh
  - Performance metrics computation
  - Data source health checks
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from core.kai_betting.db import get_db, init_db
from core.kai_betting.prediction_engine import PredictionEngine
from core.kai_betting.odds_engine import OddsEngine
from core.kai_betting.subscriptions import SubscriptionManager
from core.kai_betting.performance import PerformanceTracker
from core.kai_betting.api import _ensure_prediction_saved
from core.kai_betting.data_ingestion import DataIngestionManager

logger = logging.getLogger(__name__)


class KaiBettingWorkers:
    """Collection of scheduled automation tasks.

    Invoked from the Kai scheduler loop. Each worker is idempotent
    and safe to run multiple times per cycle.
    """

    def __init__(self):
        self._prediction_engine = PredictionEngine()
        self._subscription_mgr = SubscriptionManager()
        self._perf_tracker = PerformanceTracker()
        self._ingestion = DataIngestionManager(engine=self._prediction_engine)

    # ── Data Ingestion (real odds from external providers) ──────────────────

    def refresh_events(self) -> Dict[str, Any]:
        """Fetch upcoming events from data providers and upsert into DB.

        Delegates to DataIngestionManager which handles rate limiting,
        interval gating, and provider-specific logic.
        """
        return self._ingestion.refresh_events()

    def refresh_sync(self, with_odds: bool = True,
                     with_results: bool = True) -> Dict[str, Any]:
        """Fetch odds and/or scores for existing events.

        Delegates to DataIngestionManager which handles rate limiting,
        interval gating, and provider-specific logic.
        """
        return self._ingestion.refresh_sync(with_odds=with_odds, with_results=with_results)

    # ── Daily Prediction Generation ──────────────────────────────────────────

    def generate_daily_predictions(self, sport_keys: Optional[List[str]] = None) -> Dict[str, Any]:
        """Generate predictions for today's events using REAL odds from Odds-API.io.

        Delegates to the DataIngestionManager to fetch real odds, discover all
        markets, run predictions, and apply quality filters.
        Falls back to DB-only predictions only if the API is unavailable.
        """
        # Primary: real odds from Odds-API.io v3
        try:
            result = self._ingestion._sync_odds_v3(max_events=15)
            picks = result.get("picks", [])
            return {
                "generated": result.get("predictions_generated", 0),
                "qualified": result.get("qualified_predictions", 0),
                "unique_picks": result.get("unique_picks", 0),
                "published": result.get("unique_picks", 0),
                "errors": result.get("errors", 0),
                "source": "odds_api_io",
                "events_with_odds": result.get("events_with_odds", 0),
                "markets_discovered": result.get("markets_discovered", 0),
            }
        except Exception as e:
            logger.error(f"Daily predictions via Odds-API.io failed: {e}", exc_info=True)
            return {
                "generated": 0,
                "published": 0,
                "errors": 1,
                "source": "error",
                "error": str(e)[:200],
            }

    def _check_zero_picks_alert(self, ingestion_result: Dict[str, Any]) -> Dict[str, Any]:
        """Alert on Telegram if games were available but zero predictions qualified.

        generate_daily_predictions() can legitimately return zero picks when
        every candidate event failed the quality/edge/odds filters or the
        tier 1-3 league filter — that used to fail silently. This makes it
        visible instead of assuming "no picks" means "no games".
        """
        events_with_odds = ingestion_result.get("events_with_odds", 0)
        qualified = ingestion_result.get("qualified", 0)
        if events_with_odds == 0 or qualified > 0:
            return {"alerted": False}

        chat_id = os.environ.get("BETTING_ADMIN_CHAT_ID", "").strip()
        if not chat_id:
            logger.warning(
                "Zero qualified predictions from %d candidate events, but "
                "BETTING_ADMIN_CHAT_ID is not set — cannot alert", events_with_odds
            )
            return {"alerted": False, "reason": "no_admin_chat_id"}

        from core.kai_betting.telegram_bot import BettingTelegramBot
        text = (
            f"⚠️ Kai Betting: {events_with_odds} tier 1-3 game(s) had odds today, "
            f"but 0 cleared the quality filters — no picks were published. "
            f"Check odds-api.io coverage / quality_filters thresholds."
        )
        sent = BettingTelegramBot.send_raw(chat_id, text)
        return {"alerted": sent}

    # ── Auto-Settlement ──────────────────────────────────────────────────────

    def auto_settle_finished(self) -> Dict[str, Any]:
        """Auto-settle predictions for finished events.

        Checks events with status 'finished' and settles any pending
        predictions against those events.
        """
        with get_db() as db:
            auto_settle = db.execute(
                "SELECT value FROM betting_config WHERE key = 'auto_settle'"
            ).fetchone()
            if not auto_settle or auto_settle["value"] != "true":
                return {"settled": 0, "reason": "auto_settle disabled"}

            # Find finished events with pending predictions
            rows = db.execute("""
                SELECT p.id as prediction_id, p.selection, p.market_type,
                       e.id as event_id, e.home_score, e.away_score,
                       e.status as event_status
                FROM predictions p
                JOIN events e ON e.id = p.event_id
                WHERE p.status IN ('pending', 'published', 'quality_check', 'approved')
                  AND e.status = 'finished'
                  AND e.home_score IS NOT NULL
                  AND e.away_score IS NOT NULL
            """).fetchall()

        settled = 0
        for row in rows:
            outcome = self._determine_outcome(
                row["market_type"],
                row["selection"],
                row["home_score"],
                row["away_score"],
            )

            if outcome:
                with get_db() as db:
                    db.execute(
                        "INSERT OR REPLACE INTO prediction_results "
                        "(prediction_id, outcome, actual_score_home, actual_score_away, settled_by) "
                        "VALUES (?, ?, ?, ?, 'auto')",
                        (row["prediction_id"], outcome, row["home_score"], row["away_score"])
                    )
                    db.execute(
                        "UPDATE predictions SET status = ?, settled_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                        (outcome, row["prediction_id"])
                    )
                    db.commit()
                settled += 1

        logger.info(f"Auto-settled {settled} predictions")
        return {"settled": settled}

    def expire_stale_predictions(self, max_age_hours: int = 48) -> Dict[str, Any]:
        """Expire predictions that are too old to still be legitimately unsettled.

        auto_settle_finished() only settles predictions whose event reaches
        status='finished' with scores. A prediction whose event never gets
        there (e.g. broken/empty event_time blocking event_is_started() and
        cross-provider settlement matching) stays 'published' forever — and
        keeps being sent as a "pick" by send_picks_to() indefinitely. Any
        game is long over after max_age_hours, so treat still-unsettled
        predictions past that age as stale rather than live.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        with get_db() as db:
            cursor = db.execute("""
                UPDATE predictions SET status = 'expired', updated_at = datetime('now')
                WHERE status IN ('pending', 'published', 'quality_check', 'approved')
                  AND created_at < ?
            """, (cutoff,))
            expired = cursor.rowcount
            db.commit()

        logger.info(f"Expired {expired} stale unsettled predictions (>{max_age_hours}h old)")
        return {"expired": expired}

    # ── Subscription Expiry ─────────────────────────────────────────────────

    def check_subscription_expiry(self) -> Dict[str, Any]:
        """Expire lapsed subscriptions."""
        expired = self._subscription_mgr.expire_check()
        return {"expired": expired}

    # ── Odds Group Refresh ──────────────────────────────────────────────────

    def refresh_odds_groups(self) -> Dict[str, Any]:
        """Refresh active odds groups.

        Expires groups older than 24 hours with no activity,
        generates new groups for each risk level if needed.
        """
        with get_db() as db:
            # Expire old groups
            yesterday = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            cursor = db.execute(
                "UPDATE odds_groups SET status = 'expired' WHERE status = 'active' AND created_at < ?",
                (yesterday,)
            )
            expired = cursor.rowcount

            # Count active groups
            active_count = db.execute(
                "SELECT COUNT(*) as cnt FROM odds_groups WHERE status = 'active'"
            ).fetchone()["cnt"]
            db.commit()

        # Generate new groups if needed (minimum 5 active per risk level)
        generated = 0
        if active_count < 10:
            odds_engine = OddsEngine(self._prediction_engine)
            risk_levels = ["conservative", "moderate", "aggressive"]
            targets = [5, 10, 50]

            for risk, target in zip(risk_levels, targets):
                try:
                    result = odds_engine.generate(
                        target_odds=float(target),
                        risk_level=risk,
                        min_selections=2,
                        max_selections=8,
                    )
                    if result.selections:
                        with get_db() as db:
                            cursor = db.execute("""
                                INSERT INTO odds_groups (
                                    target_odds, label, risk_level, combined_odds,
                                    estimated_probability, average_confidence,
                                    num_selections, status
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
                                pred_id = _ensure_prediction_saved(db, sel)
                                db.commit()  # flush so FK is visible
                                db.execute(
                                    "INSERT INTO odds_group_selections "
                                    "(odds_group_id, prediction_id, sort_order) VALUES (?, ?, ?)",
                                    (group_id, pred_id, i + 1)
                                )
                            db.commit()
                        generated += 1
                except Exception as e:
                    logger.warning(f"Odds group refresh failed for {risk} {target}: {e}", exc_info=True)

        logger.info(f"Odds groups: {expired} expired, {generated} generated")
        return {"expired": expired, "generated": generated, "active_remaining": active_count}

    # ── Performance Metrics ─────────────────────────────────────────────────

    def update_performance_metrics(self) -> Dict[str, Any]:
        """Refresh performance metrics table."""
        self._perf_tracker.update_metrics_table()

        # Also compute daily metrics
        metrics = self._perf_tracker.get_metrics(period="daily")
        return {"win_rate": metrics.get("win_rate", 0), "total": metrics.get("total_predictions", 0)}

    # ── Run All Workers ─────────────────────────────────────────────────────

    def run_cycle(self) -> Dict[str, Any]:
        """Run the full automation cycle.

        Called from the Kai scheduler loop.
        """
        init_db()  # Ensure DB exists

        results = {}

        # Check subscriptions first (always run)
        try:
            results["subscriptions"] = self.check_subscription_expiry()
        except Exception as e:
            results["subscriptions_error"] = str(e)

        # Refresh events from external data providers (interval-gated)
        try:
            results["events_refresh"] = self.refresh_events()
        except Exception as e:
            results["events_refresh_error"] = str(e)

        # Sync odds and results from external data providers (interval-gated)
        try:
            results["odds_sync"] = self.refresh_sync()
        except Exception as e:
            import traceback
            logger.error(f"refresh_sync failed: {e}", exc_info=True)
            results["odds_sync_error"] = str(e)

        # Auto-settle finished events
        try:
            results["settlement"] = self.auto_settle_finished()
        except Exception as e:
            results["settlement_error"] = str(e)

        # Expire predictions too old to still be legitimately unsettled
        try:
            results["stale_expiry"] = self.expire_stale_predictions()
        except Exception as e:
            results["stale_expiry_error"] = str(e)

        # Generate daily predictions at configured time
        try:
            results["predictions"] = self.generate_daily_predictions()
            results["zero_picks_alert"] = self._check_zero_picks_alert(results["predictions"])
        except Exception as e:
            results["predictions_error"] = str(e)

        # Refresh odds groups
        try:
            results["odds_groups"] = self.refresh_odds_groups()
        except Exception as e:
            results["odds_groups_error"] = str(e)

        # Send Telegram notifications for published picks
        try:
            results["telegram_notify"] = self._notify_telegram()
        except Exception as e:
            results["telegram_notify_error"] = str(e)

        # Update performance
        try:
            results["performance"] = self.update_performance_metrics()
        except Exception as e:
            results["performance_error"] = str(e)

        logger.info(f"Worker cycle complete: {results}")
        return results

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _get_markets_for_sport(self, db, sport_key: str) -> List[str]:
        """Get available market types for a sport from the database."""
        # Return predefined market types per sport
        markets_map = {
            "football": ["match_result", "double_chance", "over_under", "btts"],
            "basketball": ["match_result", "over_under"],
            "tennis": ["match_result", "over_under"],
            "baseball": ["match_result", "over_under"],
            "ice_hockey": ["match_result", "over_under"],
            "american_football": ["match_result", "over_under"],
        }
        return markets_map.get(sport_key, ["match_result"])

    @staticmethod
    def _notify_telegram() -> Dict[str, Any]:
        """Send published predictions via Telegram if configured.

        Forces a fresh sync from Odds-API.io to get the latest picks,
        then sends them via Telegram. Interval-gated at 6 hours.
        """
        try:
            from core.kai_betting.telegram_bot import BettingTelegramBot
            from core.kai_betting.db import get_db
            from datetime import datetime, timezone

            with get_db() as db:
                row = db.execute(
                    "SELECT value FROM betting_config WHERE key = 'last_telegram_notify'"
                ).fetchone()

                if row and row["value"]:
                    try:
                        last = datetime.fromisoformat(row["value"])
                        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                        if elapsed < 6 * 3600:  # 6-hour cooldown
                            return {"sent": 0, "reason": "cooldown", "elapsed_seconds": int(elapsed)}
                    except (ValueError, OSError):
                        pass

            result = BettingTelegramBot.send_daily_notifications()

            # Mark notified
            with get_db() as db:
                now = datetime.now(timezone.utc).isoformat()
                db.execute(
                    "INSERT OR REPLACE INTO betting_config (key, value) VALUES "
                    "('last_telegram_notify', ?)", (now,)
                )

            return result
        except ImportError as e:
            return {"sent": 0, "error": str(e)}
    def _determine_outcome(
        market_type: str,
        selection: str,
        home_score: int,
        away_score: int,
    ) -> Optional[str]:
        """Determine prediction outcome from actual scores."""
        selection_lower = selection.lower()

        if market_type == "match_result":
            if home_score > away_score:
                winner = "home"
            elif away_score > home_score:
                winner = "away"
            else:
                winner = "draw"
            return "won" if selection_lower == winner else ("push" if selection_lower == "draw" else "lost")

        elif market_type == "double_chance":
            if home_score > away_score and selection_lower in ("1x", "12", "home"):
                return "won"
            elif away_score > home_score and selection_lower in ("x2", "12", "away"):
                return "won"
            elif home_score == away_score and selection_lower in ("1x", "x2"):
                return "won"
            return "lost"

        elif market_type == "draw_no_bet":
            if home_score == away_score:
                return "push"
            if home_score > away_score and selection_lower == "home":
                return "won"
            if away_score > home_score and selection_lower == "away":
                return "won"
            return "lost"

        elif market_type == "over_under":
            total = home_score + away_score
            try:
                line = float(market_type.split("_")[-1]) if "_" in market_type else 2.5
            except ValueError:
                line = 2.5
            is_over = total > line
            if (is_over and selection_lower == "over") or (not is_over and selection_lower == "under"):
                return "won"
            return "lost"

        elif market_type == "btts":
            both_scored = home_score > 0 and away_score > 0
            if (both_scored and selection_lower == "yes") or (not both_scored and selection_lower == "no"):
                return "won"
            return "lost"

        # Default: can't determine
        return None


# ── Scheduler Integration ────────────────────────────────────────────────────

# Singleton instance for the Kai scheduler to call
_worker_instance: Optional[KaiBettingWorkers] = None


def get_workers() -> KaiBettingWorkers:
    """Get or create the singleton worker instance."""
    global _worker_instance
    if _worker_instance is None:
        _worker_instance = KaiBettingWorkers()
    return _worker_instance


def run_betting_cycle() -> Dict[str, Any]:
    """Entry point for Kai scheduler integration.

    Import and call this from core/scheduler.py:
        from core.kai_betting.workers import run_betting_cycle
        betting_results = run_betting_cycle()
    """
    return get_workers().run_cycle()
