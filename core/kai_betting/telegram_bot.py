"""Kai Betting — Telegram Bot.

Handles Telegram integration for Kai Betting:
- /start, /help — onboarding
- /picks — latest predictions
- /odds — odds groups
- /subscribe — subscription flow
- /results — recent results
- /performance — performance stats

Integrates with the main Kai Telegram infrastructure via the betting router.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Callable

from core.kai_betting.db import get_db
from core.kai_betting.subscriptions import SubscriptionManager
from core.kai_betting.performance import PerformanceTracker

logger = logging.getLogger(__name__)


class BettingTelegramBot:
    """Telegram bot handler for Kai Betting.

    Designed to be called from the main Kai Telegram poller or as a standalone
    bot. Handles command parsing, subscription checks, and formatted responses.
    """

    def __init__(self):
        self._subscription_mgr = SubscriptionManager()
        self._perf_tracker = PerformanceTracker()

    # ── Command Routing ──────────────────────────────────────────────────────

    def handle_message(self, chat_id: str, text: str, user_id: Optional[str] = None) -> str:
        """Route an incoming message to the appropriate handler.

        Args:
            chat_id: Telegram chat ID for responses
            text: Raw message text
            user_id: Optional Telegram user ID for personalization

        Returns:
            Formatted response text
        """
        text = text.strip()

        if text.startswith("/start"):
            return self._cmd_start()
        elif text.startswith("/help"):
            return self._cmd_help()
        elif text.startswith("/picks") or text.startswith("/predictions"):
            return self._cmd_picks(text)
        elif text.startswith("/odds"):
            return self._cmd_odds(text)
        elif text.startswith("/subscribe"):
            return self._cmd_subscribe(chat_id, user_id)
        elif text.startswith("/results"):
            return self._cmd_results()
        elif text.startswith("/performance") or text.startswith("/stats"):
            return self._cmd_performance()
        elif text.startswith("/sports"):
            return self._cmd_sports()
        elif text.startswith("/myaccount"):
            return self._cmd_myaccount(chat_id, user_id)
        else:
            return self._cmd_help()

    # ── Command Handlers ─────────────────────────────────────────────────────

    def _cmd_start(self) -> str:
        return (
            "🎯 *Kai Betting*\n\n"
            "AI-powered sports predictions across 10 sports.\n\n"
            "• `/picks` — Today's predictions\n"
            "• `/odds` — Odds groups (accumulators)\n"
            "• `/results` — Recent results\n"
            "• `/performance` — Win rate & ROI stats\n"
            "• `/sports` — Supported sports\n"
            "• `/subscribe` — Get premium access\n"
            "• `/help` — All commands\n\n"
            "_Free tier: 3 picks/day. Premium: unlimited._"
        )

    def _cmd_help(self) -> str:
        return (
            "📋 *Kai Betting Commands*\n\n"
            "*Predictions*\n"
            "`/picks` — Today's published predictions\n"
            "`/picks football` — Filter by sport\n"
            "`/picks high` — High confidence only\n\n"
            "*Odds Groups*\n"
            "`/odds` — Active accumulators\n"
            "`/odds 10` — Show 10 ODDS groups\n"
            "`/odds 50 moderate` — 50 ODDS, moderate risk\n\n"
            "*Results & Stats*\n"
            "`/results` — Last 10 results\n"
            "`/performance` — Win rate, ROI, calibration\n"
            "`/sports` — Supported sports list\n\n"
            "*Account*\n"
            "`/subscribe` — Get premium access\n"
            "`/myaccount` — Subscription status\n\n"
            "*Notifications*\n"
            "Set up in the web dashboard: picks, results, odds alerts"
        )

    def _cmd_picks(self, text: str) -> str:
        """Show today's predictions with optional filters."""
        parts = text.split()
        sport_filter = None
        quality_filter = None

        for part in parts[1:]:
            part_lower = part.lower()
            if part_lower in ("football", "basketball", "tennis", "baseball",
                              "ice_hockey", "american_football", "rugby",
                              "volleyball", "handball", "cricket"):
                sport_filter = part_lower
            elif part_lower in ("high", "medium", "low"):
                quality_filter = part_lower

        with get_db() as db:
            query = """
                SELECT p.*, s.key as sport_key, s.name as sport_name
                FROM predictions p
                JOIN sports s ON s.id = p.sport_id
                WHERE p.status = 'published'
            """
            params: list = []

            if sport_filter:
                query += " AND s.key = ?"
                params.append(sport_filter)
            if quality_filter == "high":
                query += " AND p.confidence >= 75"
            elif quality_filter == "medium":
                query += " AND p.confidence >= 50 AND p.confidence < 75"
            elif quality_filter == "low":
                query += " AND p.confidence < 50"

            query += " ORDER BY p.confidence DESC LIMIT 10"

            rows = db.execute(query, params).fetchall()

        if not rows:
            return (
                "📊 *Today's Picks*\n\n"
                "_No published predictions yet. Check back soon or use `/subscribe` for premium early access._"
            )

        lines = [f"📊 *Today's Picks* ({len(rows)})\n"]
        for i, row in enumerate(rows, 1):
            emoji = "🟢" if row["confidence"] >= 70 else ("🟡" if row["confidence"] >= 50 else "🔴")
            odds_str = f" @ {row['bookmaker_odds']:.2f}" if row["bookmaker_odds"] else ""
            lines.append(
                f"{i}. {emoji} {row['sport_name']} — {row['market_name']}\n"
                f"   Selection: *{row['selection'].upper()}*{odds_str}\n"
                f"   Confidence: {row['confidence']:.0f}% | Edge: {row['edge']:.1%}" if row['edge'] else
                f"   Confidence: {row['confidence']:.0f}%"
            )

        return "\n".join(lines)

    def _cmd_odds(self, text: str) -> str:
        """Show active odds groups with optional filtering."""
        parts = text.split()
        target_filter = None
        risk_filter = None

        for part in parts[1:]:
            try:
                odds = float(part)
                target_filter = odds
            except ValueError:
                if part.lower() in ("conservative", "moderate", "aggressive", "high_risk"):
                    risk_filter = part.lower()

        with get_db() as db:
            query = "SELECT * FROM odds_groups WHERE status = 'active'"
            params: list = []

            if target_filter:
                query += " AND target_odds = ?"
                params.append(target_filter)
            if risk_filter:
                query += " AND risk_level = ?"
                params.append(risk_filter)

            query += " ORDER BY target_odds ASC LIMIT 5"
            rows = db.execute(query, params).fetchall()

        if not rows:
            return (
                "🎯 *Odds Groups*\n\n"
                "_No active odds groups. Premium subscribers get priority access._"
            )

        lines = [f"🎯 *Odds Groups* ({len(rows)})\n"]
        for i, row in enumerate(rows, 1):
            risk_emoji = {"conservative": "🛡️", "moderate": "⚖️", "aggressive": "🔥", "high_risk": "💎"}.get(
                row["risk_level"], "📊"
            )
            lines.append(
                f"{i}. {risk_emoji} *{row['label']}* ({row['risk_level']})\n"
                f"   Combined odds: {row['combined_odds']:.2f} | "
                f"Selections: {row['num_selections']} | "
                f"Conf: {row['average_confidence']:.0f}%"
            )

        return "\n".join(lines)

    def _cmd_subscribe(self, chat_id: str, user_id: Optional[str]) -> str:
        """Show subscription options."""
        with get_db() as db:
            plans = db.execute(
                "SELECT * FROM subscription_plans WHERE is_active = 1 ORDER BY duration_days"
            ).fetchall()

        lines = ["💳 *Subscription Plans*\n"]
        for plan in plans:
            per_day = plan["price"] / plan["duration_days"]
            lines.append(
                f"• *{plan['name']}* — GHS {plan['price']:.2f}\n"
                f"  {plan['duration_days']} day(s) | ~GHS {per_day:.2f}/day\n"
            )

        lines.append(
            "\n_Payment via Mobile Money (MTN, Telecel, AirtelTigo)._"
            "\n_Use `/subscribe` on the web dashboard to complete purchase._"
        )
        return "\n".join(lines)

    def _cmd_results(self) -> str:
        """Show recent prediction results."""
        with get_db() as db:
            rows = db.execute("""
                SELECT p.*, s.name as sport_name, s.icon as sport_icon,
                       pr.outcome, pr.actual_score_home, pr.actual_score_away
                FROM predictions p
                JOIN sports s ON s.id = p.sport_id
                LEFT JOIN prediction_results pr ON pr.prediction_id = p.id
                WHERE p.status IN ('won', 'lost', 'push', 'void')
                ORDER BY p.settled_at DESC
                LIMIT 10
            """).fetchall()

        if not rows:
            return "📈 *Recent Results*\n\n_No settled predictions yet._"

        lines = ["📈 *Recent Results*\n"]
        wins = sum(1 for r in rows if r["outcome"] == "won")
        total = len(rows)

        for i, row in enumerate(rows, 1):
            outcome_emoji = {"won": "✅", "lost": "❌", "push": "↩️", "void": "🚫"}.get(
                row["outcome"], "❓"
            )
            score = ""
            if row["actual_score_home"] is not None:
                score = f" ({row['actual_score_home']}-{row['actual_score_away']})"
            lines.append(
                f"{i}. {outcome_emoji} {row['sport_name']} — {row['market_name']}\n"
                f"   Pick: {row['selection'].upper()} | "
                f"Odds: {row['bookmaker_odds']:.2f}{score}" if row['bookmaker_odds'] else
                f"{i}. {outcome_emoji} {row['sport_name']} — {row['market_name']}\n"
                f"   Pick: {row['selection'].upper()}{score}"
            )

        win_rate = (wins / total * 100) if total > 0 else 0
        lines.append(f"\n_Win rate: {wins}/{total} ({win_rate:.0f}%)_")
        return "\n".join(lines)

    def _cmd_performance(self) -> str:
        """Show performance statistics."""
        perf = self._perf_tracker.get_metrics(period="all_time")

        lines = [
            "📊 *Performance Stats*\n",
            f"• Total predictions: {perf['total_predictions']}",
            f"• Win rate: *{perf['win_rate']:.1f}%*",
            f"• ROI: *{perf['roi']:.1f}%*",
            f"• P/L: {perf['profit_loss']:+.2f} units",
            f"• Avg odds: {perf['average_odds']:.2f}",
            f"• Avg confidence: {perf['average_confidence']:.0f}%",
            "\n*By Sport:*",
        ]

        for sport in perf.get("by_sport", [])[:5]:
            lines.append(f"• {sport['sport_name']}: {sport['win_rate']:.0f}% ({sport['total']} picks)")

        return "\n".join(lines)

    def _cmd_sports(self) -> str:
        """Show supported sports."""
        with get_db() as db:
            rows = db.execute(
                "SELECT * FROM sports WHERE is_active = 1 ORDER BY sort_order"
            ).fetchall()

        lines = ["🏟️ *Supported Sports*\n"]
        for row in rows:
            lines.append(f"{row['icon']} *{row['name']}* (`{row['key']}`)")

        return "\n".join(lines)

    def _cmd_myaccount(self, chat_id: str, user_id: Optional[str]) -> str:
        """Show user's subscription status."""
        if not user_id:
            return "🔐 *My Account*\n\n_Link your Telegram account on the web dashboard first._"

        with get_db() as db:
            ta = db.execute(
                "SELECT user_id FROM telegram_accounts WHERE telegram_id = ? AND is_active = 1",
                (user_id,)
            ).fetchone()

            if not ta:
                return "🔐 *My Account*\n\n_Your Telegram is not linked. Register on the web dashboard first._"

            access = self._subscription_mgr.check_access(ta["user_id"])

        if access["has_access"]:
            return (
                f"🔐 *My Account*\n\n"
                f"Plan: *{access['plan_name']}*\n"
                f"Expires: {access['expires_at'][:10] if access['expires_at'] else 'N/A'}\n"
                f"Auto-renew: {'Yes' if access.get('auto_renew') else 'No'}\n"
                f"Daily picks: {access['limits']['max_picks']}"
            )
        else:
            return (
                "🔐 *My Account*\n\n"
                "Plan: *Free Tier*\n"
                f"Daily picks: 3\n\n"
                "_Use `/subscribe` to upgrade._"
            )

    # ── Formatters ────────────────────────────────────────────────────────────

    @staticmethod
    def format_prediction(row: Dict[str, Any]) -> str:
        """Format a single prediction for Telegram."""
        emoji = "🟢" if row.get("confidence", 0) >= 70 else ("🟡" if row.get("confidence", 0) >= 50 else "🔴")
        odds = row.get("bookmaker_odds")
        odds_str = f" @ {odds:.2f}" if odds else ""
        return (
            f"{emoji} {row.get('sport_name', row.get('sport_key', ''))} — "
            f"{row.get('market_name', '')}\n"
            f"Pick: *{row.get('selection', '').upper()}*{odds_str}\n"
            f"Conf: {row.get('confidence', 0):.0f}%"
        )

    @staticmethod
    def format_odds_group(row: Dict[str, Any]) -> str:
        """Format an odds group for Telegram."""
        risk_emoji = {"conservative": "🛡️", "moderate": "⚖️", "aggressive": "🔥", "high_risk": "💎"}
        emoji = risk_emoji.get(row.get("risk_level", ""), "📊")
        return (
            f"{emoji} *{row.get('label', '')}*\n"
            f"Combined: {row.get('combined_odds', 0):.2f} | "
            f"Selections: {row.get('num_selections', 0)}\n"
            f"Confidence: {row.get('average_confidence', 0):.0f}% | "
            f"Risk: {row.get('risk_level', '')}"
        )
