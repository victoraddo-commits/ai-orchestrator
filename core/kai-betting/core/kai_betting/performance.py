"""Kai Betting — Performance Analytics.

Tracks prediction accuracy, ROI, and calibration metrics.
Computes per-sport, per-market, and overall performance stats.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from core.kai_betting.db import get_db

logger = logging.getLogger(__name__)


class PerformanceTracker:
    """Tracks and analyzes prediction performance over time."""

    def get_metrics(
        self,
        period: str = "all_time",
        sport_key: Optional[str] = None,
        market_type: Optional[str] = None,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Get performance metrics for a given period.

        Args:
            period: 'daily', 'weekly', 'monthly', 'all_time'
            sport_key: Optional sport filter
            market_type: Optional market type filter
            days: Lookback in days (for period-based queries)

        Returns:
            Dict with win_rate, roi, profit_loss, and breakdowns
        """
        with get_db() as db:
            period_start = self._period_start(period, days)

            query = """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN p.status = 'won' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN p.status = 'lost' THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN p.status = 'push' THEN 1 ELSE 0 END) as pushes,
                    SUM(CASE WHEN p.status = 'void' THEN 1 ELSE 0 END) as voids,
                    AVG(p.bookmaker_odds) as avg_odds,
                    AVG(p.confidence) as avg_confidence
                FROM predictions p
                JOIN sports s ON s.id = p.sport_id
                WHERE p.status IN ('won', 'lost', 'push', 'void')
            """
            params: list = []

            if period_start:
                query += " AND p.created_at >= ?"
                params.append(period_start)

            if sport_key:
                query += " AND s.key = ?"
                params.append(sport_key)

            if market_type:
                query += " AND p.market_type = ?"
                params.append(market_type)

            row = db.execute(query, params).fetchone()

            total = row["total"] if row else 0
            wins = row["wins"] or 0
            losses = row["losses"] or 0
            pushes = row["pushes"] or 0
            voids = row["voids"] or 0
            settled = wins + losses + pushes
            win_rate = (wins / settled * 100) if settled > 0 else 0.0

            # ROI: assume 1 unit stake per prediction
            total_stake = settled
            total_return = sum(
                row["avg_odds"] or 0 for _ in range(wins)
            ) if wins > 0 else 0
            roi = ((total_return - total_stake) / total_stake * 100) if total_stake > 0 else 0.0
            profit_loss = total_return - total_stake

            # Per-sport breakdown
            sport_breakdown = self._sport_breakdown(db, period_start)

            return {
                "period": period,
                "period_start": period_start or "beginning",
                "period_end": datetime.now(timezone.utc).isoformat(),
                "sport_key": sport_key,
                "market_type": market_type,
                "total_predictions": total,
                "wins": wins,
                "losses": losses,
                "pushes": pushes,
                "voids": voids,
                "win_rate": round(win_rate, 2),
                "roi": round(roi, 2),
                "average_odds": round(row["avg_odds"] or 0, 2),
                "average_confidence": round(row["avg_confidence"] or 0, 2),
                "profit_loss": round(profit_loss, 2),
                "by_sport": sport_breakdown,
            }

    def get_dashboard_summary(self) -> Dict[str, Any]:
        """Get a complete dashboard summary."""
        with get_db() as db:
            # User count
            total_users = db.execute("SELECT COUNT(*) as cnt FROM users WHERE is_active = 1").fetchone()["cnt"]

            # Active subscriptions
            active_subs = db.execute(
                "SELECT COUNT(*) as cnt FROM subscriptions WHERE status = 'active'"
            ).fetchone()["cnt"]

            # Predictions
            total_preds = db.execute("SELECT COUNT(*) as cnt FROM predictions").fetchone()["cnt"]
            published_preds = db.execute(
                "SELECT COUNT(*) as cnt FROM predictions WHERE status = 'published'"
            ).fetchone()["cnt"]

            # Overall performance
            perf = self.get_metrics(period="all_time")

            # Revenue
            total_revenue = db.execute(
                "SELECT COALESCE(SUM(amount), 0) as total FROM payments WHERE status = 'completed'"
            ).fetchone()["total"]

            # Active odds groups
            active_groups = db.execute(
                "SELECT COUNT(*) as cnt FROM odds_groups WHERE status = 'active'"
            ).fetchone()["cnt"]

            # Sports coverage
            sports_coverage = db.execute("""
                SELECT s.key, s.name, s.icon, COUNT(p.id) as prediction_count
                FROM sports s
                LEFT JOIN predictions p ON p.sport_id = s.id
                WHERE s.is_active = 1
                GROUP BY s.id
                ORDER BY prediction_count DESC
            """).fetchall()

            # Recent performance (last 7 days)
            recent = self._recent_daily_performance(db, days=7)

            return {
                "total_users": total_users,
                "active_subscriptions": active_subs,
                "total_predictions": total_preds,
                "published_predictions": published_preds,
                "overall_win_rate": perf["win_rate"],
                "overall_roi": perf["roi"],
                "total_revenue": round(total_revenue, 2),
                "active_odds_groups": active_groups,
                "sports_coverage": [
                    {"key": r["key"], "name": r["name"], "icon": r["icon"], "count": r["prediction_count"]}
                    for r in sports_coverage
                ],
                "recent_performance": recent,
            }

    def calibrate(self) -> Dict[str, Any]:
        """Run calibration analysis — check if predicted probabilities match outcomes.

        Good calibration: predictions at 70% confidence should win ~70% of the time.
        """
        with get_db() as db:
            rows = db.execute("""
                SELECT p.estimated_probability, p.confidence, p.status
                FROM predictions p
                WHERE p.status IN ('won', 'lost', 'push', 'void')
            """).fetchall()

            if not rows:
                return {"status": "insufficient_data", "message": "No settled predictions yet"}

            # Bucket predictions by confidence decile
            buckets: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                decile = min(9, int(row["confidence"] / 10))
                bucket_key = f"{decile * 10}-{(decile + 1) * 10}"
                if bucket_key not in buckets:
                    buckets[bucket_key] = {"total": 0, "wins": 0}
                buckets[bucket_key]["total"] += 1
                if row["status"] == "won":
                    buckets[bucket_key]["wins"] += 1

            calibration = {}
            for bucket_key, data in sorted(buckets.items()):
                actual_rate = (data["wins"] / data["total"] * 100) if data["total"] > 0 else 0
                expected_mid = int(bucket_key.split("-")[0]) + 5
                calibration[bucket_key] = {
                    "total": data["total"],
                    "wins": data["wins"],
                    "actual_win_rate": round(actual_rate, 1),
                    "expected_midpoint": expected_mid,
                    "calibration_error": round(actual_rate - expected_mid, 1),
                }

            return {
                "status": "complete",
                "total_settled": len(rows),
                "calibration": calibration,
            }

    def update_metrics_table(self) -> None:
        """Refresh the performance_metrics table with latest aggregates."""
        metrics = self.get_metrics(period="all_time")

        with get_db() as db:
            db.execute("""
                INSERT OR REPLACE INTO performance_metrics (
                    period, period_start, period_end,
                    total_predictions, wins, losses, pushes, voids,
                    win_rate, roi, average_odds, average_confidence, profit_loss
                ) VALUES ('all_time', '2026-01-01', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                metrics["total_predictions"],
                metrics["wins"],
                metrics["losses"],
                metrics["pushes"],
                metrics["voids"],
                metrics["win_rate"],
                metrics["roi"],
                metrics["average_odds"],
                metrics["average_confidence"],
                metrics["profit_loss"],
            ))
            db.commit()

    # ── Internal Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _period_start(period: str, days: int) -> Optional[str]:
        """Get the start timestamp for a period."""
        now = datetime.now(timezone.utc)
        if period == "daily":
            return (now - timedelta(days=1)).isoformat()
        elif period == "weekly":
            return (now - timedelta(days=7)).isoformat()
        elif period == "monthly":
            return (now - timedelta(days=days)).isoformat()
        elif period == "all_time":
            return None
        else:
            return (now - timedelta(days=days)).isoformat()

    def _sport_breakdown(self, db, period_start: Optional[str]) -> List[Dict[str, Any]]:
        """Get per-sport performance breakdown."""
        query = """
            SELECT s.key, s.name,
                   COUNT(*) as total,
                   SUM(CASE WHEN p.status = 'won' THEN 1 ELSE 0 END) as wins
            FROM predictions p
            JOIN sports s ON s.id = p.sport_id
            WHERE p.status IN ('won', 'lost', 'push', 'void')
        """
        params: list = []
        if period_start:
            query += " AND p.created_at >= ?"
            params.append(period_start)
        query += " GROUP BY s.id ORDER BY total DESC"

        rows = db.execute(query, params).fetchall()
        breakdown = []
        for row in rows:
            total = row["total"]
            wins = row["wins"] or 0
            losses = (row["total"] or 0) - wins
            win_rate = (wins / total * 100) if total > 0 else 0
            breakdown.append({
                "sport_key": row["key"],
                "sport_name": row["name"],
                "total": total,
                "wins": wins,
                "win_rate": round(win_rate, 1),
            })
        return breakdown

    def _recent_daily_performance(self, db, days: int = 7) -> List[Dict[str, Any]]:
        """Get day-by-day performance for the last N days."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        rows = db.execute("""
            SELECT date(p.created_at) as day,
                   COUNT(*) as total,
                   SUM(CASE WHEN p.status = 'won' THEN 1 ELSE 0 END) as wins,
                   AVG(p.bookmaker_odds) as avg_odds
            FROM predictions p
            WHERE p.status IN ('won', 'lost', 'push', 'void')
              AND p.created_at >= ?
            GROUP BY day
            ORDER BY day ASC
        """, (since,)).fetchall()

        return [
            {
                "date": r["day"],
                "total": r["total"],
                "wins": r["wins"],
                "win_rate": round((r["wins"] / r["total"] * 100) if r["total"] > 0 else 0, 1),
                "avg_odds": round(r["avg_odds"] or 0, 2),
            }
            for r in rows
        ]
