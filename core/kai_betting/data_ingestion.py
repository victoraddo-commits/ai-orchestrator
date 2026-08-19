"""Kai Betting — Data Ingestion Manager (Odds-API.io v3).

Orchestrates the real-data pipeline:
  Odds-API.io → Real Events → Real Odds → All Markets → Predictions → Quality Filter → Store

Uses OddsAPIioSource as primary. Falls back to legacy OddsAPISource if unavailable.
With LIVE_DATA_MODE=true, synthetic/placeholder data is rejected.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Set

from core.kai_betting.db import (
    get_db, upsert_team, upsert_league, upsert_event,
)
from core.kai_betting.data_sources.odds_api_io import (
    OddsAPIioSource, SPORT_SLUG_MAP, slug_for_kai,
    MARKET_NORMALIZATION, normalize_selection,
    market_is_predictable, sportybet_display,
)
from core.kai_betting.prediction_engine import PredictionEngine

logger = logging.getLogger(__name__)

# ── Quality Thresholds ─────────────────────────────────────────────────────────
# Configurable via betting_config; these are defaults.

DEFAULT_QUALITY = {
    "min_probability": 0.65,
    "min_confidence": 70.0,
    "min_data_quality": 70.0,
    "require_positive_edge": True,
    "require_real_odds": True,
}

# The 2 bookmakers allowed on the free tier
FREE_TIER_BOOKMAKERS = "Bet365,1xbet"

# ── League Tier Filter (SportyBet availability + pick quality) ───────────────
# SportyBet (sportybet.com) carries the top 1-3 divisions + marquee cups/
# continental competitions + major African leagues, but NOT the long tail of
# obscure lower divisions or amateur/reserve leagues that Bet365 lists. Picks
# are restricted to leagues that resolve to tier 1, 2, or 3 below, so users
# can actually find the game on SportyBet and the pick pool stays credible.
#
# tier 1 = top flight of a country / marquee continental or international
#          competition / the sport's premier professional league.
# tier 2 = second flight of a country / major domestic cup or college league.
# tier 3 = third flight of a country / lower-tier regional competition.
#
# Matched by substring against "<name> <slug>" (lowercased). Keywords are
# checked longest-first so a specific match (e.g. "2. bundesliga", tier 2)
# wins over a shorter generic one it contains (e.g. "bundesliga", tier 1).
# Unmatched leagues resolve to None (fail closed — excluded, not tier 3).

LEAGUE_TIER_KEYWORDS: List["tuple[str, int]"] = [
    # ── Tier 1: top flights + marquee continental/international competitions ──
    ("premier league", 1), ("premier soccer league", 1), ("npl", 1),
    ("la liga", 1), ("laliga", 1), ("primera division", 1),
    ("serie a", 1),
    ("bundesliga", 1),
    ("ligue 1", 1), ("ligue1", 1),
    ("eredivisie", 1),
    ("primeira liga", 1),
    ("super lig", 1),
    ("jupiler pro league", 1), ("first division a", 1),
    ("champions league", 1), ("europa league", 1),
    ("conference league", 1), ("nations league", 1),
    ("world cup", 1), ("copa america", 1), ("africa cup", 1), ("afcon", 1), ("euro", 1),
    ("major league soccer", 1), ("mls", 1),
    ("liga mx", 1),
    ("brasileirao", 1), ("campeonato brasileiro", 1),
    ("primera division argentina", 1),
    ("scottish premiership", 1),
    ("ghana premier league", 1), ("nigeria professional football league", 1),
    ("egypt premier league", 1), ("kenya premier league", 1),
    ("uganda premier league", 1), ("tanzania premier league", 1),
    ("zambia super league", 1),
    ("nba", 1), ("euroleague", 1), ("wnba", 1),
    ("atp", 1), ("wta", 1), ("australian open", 1), ("roland garros", 1),
    ("wimbledon", 1), ("us open", 1), ("grand slam", 1), ("masters", 1), ("davis cup", 1),
    ("mlb", 1), ("npb", 1), ("kbo", 1),
    ("nhl", 1),
    ("nfl", 1),
    ("nrl", 1), ("six nations", 1), ("premiership rugby", 1),
    # ── Tier 2: second flights + major cups + college leagues ──────────────────
    ("championship", 2),
    ("segunda division", 2), ("segunda divis", 2),
    ("serie b", 2),
    ("2. bundesliga", 2), ("2 bundesliga", 2),
    ("ligue 2", 2),
    ("first division b", 2),
    ("ncaa", 2),
    ("nbl", 2), ("acb", 2), ("liga endesa", 2),
    ("cfl", 2),
    # ── Tier 3: third flights + lower regional competitions ────────────────────
    ("league one", 3), ("league two", 3),
    ("third division", 3), ("regionalliga", 3), ("national league", 3),
]

# Sorted longest-keyword-first so specific matches beat generic ones they contain.
_LEAGUE_TIER_KEYWORDS_SORTED = sorted(LEAGUE_TIER_KEYWORDS, key=lambda kt: -len(kt[0]))


def _league_tier(league_name: str, league_slug: str = "") -> Optional[int]:
    """Resolve a league to tier 1, 2, or 3, or None if it doesn't match.

    Matches the league name (and slug) against LEAGUE_TIER_KEYWORDS.
    Unknown/empty league names resolve to None (fail closed).
    """
    haystack = f"{league_name or ''} {league_slug or ''}".lower()
    if not haystack.strip():
        return None
    for keyword, tier in _LEAGUE_TIER_KEYWORDS_SORTED:
        if keyword in haystack:
            return tier
    return None


def _is_live_data_mode(db) -> bool:
    """Check if LIVE_DATA_MODE is enabled."""
    try:
        row = db.execute(
            "SELECT value FROM betting_config WHERE key = 'live_data_mode'"
        ).fetchone()
        return row and row["value"] == "true"
    except Exception:
        return True  # default to real-data-only


class DataIngestionManager:
    """Fetches sports data from Odds-API.io v3 and generates predictions.

    Pipeline:
      1. Fetch active sports → sport slugs
      2. Fetch upcoming events per sport
      3. For each event, fetch odds (all markets)
      4. Extract & normalize all market selections
      5. Run each through PredictionEngine
      6. Apply quality filters
      7. Store qualified predictions
    """

    def __init__(self, engine: Optional[PredictionEngine] = None):
        self._engine = engine or PredictionEngine()
        self._primary = OddsAPIioSource()

    @property
    def is_configured(self) -> bool:
        return self._primary.is_configured

    @property
    def provider_status(self) -> Dict[str, Any]:
        return self._primary.provider_status

    # ── Public entry points ──────────────────────────────────────────────────

    def refresh_events(self) -> Dict[str, Any]:
        """Fetch upcoming events from Odds-API.io and upsert into DB."""
        if not self.is_configured:
            return {"status": "skipped", "reason": "ODDS_API_IO_KEY not set"}

        if not self._should_run("last_events_refresh",
                                self._config_int("events_interval_hours", 6) * 3600):
            return {"status": "skipped", "reason": "within refresh interval"}

        active_sports = self._get_active_sports()
        slugs = self._sports_to_slugs(active_sports)

        if not slugs:
            return {"status": "skipped", "reason": "no active sports"}

        total_new = 0
        total_updated = 0
        error_count = 0

        all_events = self._primary.fetch_events(slugs, days=5)

        with get_db() as db:
            for slug, events in all_events.items():
                kai_sport = SPORT_SLUG_MAP.get(slug)
                if not kai_sport:
                    continue

                sport_row = db.execute(
                    "SELECT id FROM sports WHERE key = ?", (kai_sport,)
                ).fetchone()
                if not sport_row:
                    continue
                sport_id = sport_row["id"]

                for evt in events:
                    try:
                        count = self._ingest_event_v3(db, sport_id, evt)
                        if count > 0:
                            total_new += 1
                        else:
                            total_updated += 1
                    except Exception as e:
                        logger.warning(f"Ingest failed for event {evt.get('id', '?')}: {e}")
                        error_count += 1

            self._mark_refreshed(db, "last_events_refresh")

        logger.info(
            f"refresh_events: {total_new} new, {total_updated} updated, "
            f"{error_count} errors"
        )
        return {
            "status": "ok",
            "new_events": total_new,
            "updated_events": total_updated,
            "errors": error_count,
        }

    def refresh_sync(self, with_odds: bool = True,
                     with_results: bool = True) -> Dict[str, Any]:
        """Fetch odds for upcoming events and generate predictions."""
        # SportsGameOdds is a supplemental source with its own key/quota/
        # interval — it must run regardless of whether the PRIMARY provider
        # (Odds-API.io) is configured, so it's synced before the early
        # return below rather than gated behind self.is_configured.
        try:
            sportsgameodds_result = self._sync_sportsgameodds()
        except Exception as e:
            logger.error(f"_sync_sportsgameodds failed: {e}", exc_info=True)
            sportsgameodds_result = {"status": "error", "error": str(e)}

        if not self.is_configured:
            return {
                "status": "skipped", "reason": "ODDS_API_IO_KEY not set",
                "sportsgameodds": sportsgameodds_result,
            }

        odds_result = {"status": "skipped", "reason": "within refresh interval"}
        results_result = {"status": "skipped", "reason": "within refresh interval"}

        if with_odds and self._should_run("last_odds_refresh",
                                          self._config_int("odds_interval_minutes", 60) * 60):
            odds_result = self._sync_odds_v3()

        # Results sync uses the legacy provider (odds_api) as primary, since
        # its scores are keyed by the same external_id namespace the pending
        # predictions' events live under. That provider has a single shared
        # monthly quota though (500 free-tier requests), so when it's
        # unconfigured or exhausted, fall back to SportsGameOdds' own
        # (separate, larger) quota — matched by team names + kickoff time
        # since the namespaces don't align. See _sync_results_fallback_sgo.
        if with_results and self._should_run("last_results_refresh",
                                             self._config_int("results_interval_minutes", 30) * 60):
            try:
                results_result = self._sync_results_legacy()
            except Exception as e:
                logger.error(f"_sync_results failed: {e}", exc_info=True)
                results_result = {"status": "error", "error": str(e)}

            if results_result.get("status") in ("skipped", "error"):
                try:
                    results_result["fallback"] = self._sync_results_fallback_sgo()
                except Exception as e:
                    logger.error(f"_sync_results_fallback_sgo failed: {e}", exc_info=True)
                    results_result["fallback"] = {"status": "error", "error": str(e)}

        return {
            "odds": odds_result,
            "results": results_result,
            "sportsgameodds": sportsgameodds_result,
        }

    def generate_todays_picks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Generate today's qualified picks — used for Telegram/dashboard output."""
        picks = self._sync_odds_v3(max_events=15)
        return picks.get("qualified_picks", [])[:limit]

    # ── v3 Odds Sync (primary pipeline) ──────────────────────────────────────

    def _sync_odds_v3(self, max_events: int = 15) -> Dict[str, Any]:
        """Fetch odds from Odds-API.io v3 with full market discovery.

        For each upcoming event, fetches all available markets from Bet365
        and 1xbet, normalizes them, runs predictions, and filters by quality.
        """
        active_sports = self._get_active_sports()
        slugs = self._sports_to_slugs(active_sports)

        if not slugs:
            return {"status": "skipped", "reason": "no active sports"}

        total_events_fetched = 0
        total_events_with_odds = 0
        total_markets_discovered = 0
        total_predictions = 0
        qualified_count = 0
        error_count = 0
        all_qualified: List[Dict[str, Any]] = []

        with get_db() as db:
            live_data = _is_live_data_mode(db)
            quality = self._load_quality_config(db)
            major_only = self._config_bool("major_leagues_only", True)

            for slug in slugs:
                kai_sport = SPORT_SLUG_MAP.get(slug)
                if not kai_sport:
                    continue

                sport_row = db.execute(
                    "SELECT id FROM sports WHERE key = ?", (kai_sport,)
                ).fetchone()
                if not sport_row:
                    continue
                sport_id = sport_row["id"]

                # Get upcoming events for this sport (next 5 days)
                events_data = self._primary.fetch_events([slug], days=5)
                events = events_data.get(slug, [])
                if not events:
                    continue

                # Filter to upcoming events only
                upcoming = [e for e in events
                           if e.get("status") in ("pending", "not_started", "upcoming", "open")]
                total_events_fetched += len(upcoming)

                # Restrict picks to tier 1-3 leagues (SportyBet-carried divisions).
                # Deliberately strict — no fallback to unfiltered/obscure leagues.
                # A day with zero tier 1-3 games surfaces as zero picks, which
                # generate_daily_predictions()'s zero-picks alert makes visible
                # instead of silently substituting junk-league games.
                if major_only:
                    tiered = [e for e in upcoming if (
                        _league_tier(
                            e.get("league", {}).get("name", ""),
                            e.get("league", {}).get("slug", ""),
                        ) or 99
                    ) <= 3]
                    if len(tiered) < len(upcoming):
                        logger.info(
                            f"tier filter kept {len(tiered)}/{len(upcoming)} events for {slug}"
                        )
                    upcoming = tiered

                # Process each event
                for evt in upcoming[:max_events]:
                    event_id = evt.get("id")
                    if not event_id:
                        continue

                    try:
                        # Fetch odds with ALL markets
                        odds_data = self._primary.fetch_odds(
                            event_id, slug, FREE_TIER_BOOKMAKERS
                        )

                        if not odds_data:
                            continue

                        # Check if bookmakers returned data
                        bms = odds_data.get("bookmakers", {})
                        if not bms or all(
                            not isinstance(v, list) or len(v) == 0
                            for v in bms.values()
                        ):
                            continue

                        total_events_with_odds += 1
                        home = odds_data.get("home", evt.get("home", ""))
                        away = odds_data.get("away", evt.get("away", ""))

                        # Upsert event into DB. Pass the already-resolved home/away
                        # (and odds_data's date, which fetch_events' list response
                        # can lack) so this doesn't fall back to placeholder names.
                        self._ingest_event_v3(
                            db, sport_id, evt,
                            home_name=home, away_name=away,
                            event_time=odds_data.get("date", evt.get("date", "")),
                        )

                        # Extract all markets
                        markets = OddsAPIioSource.extract_markets(odds_data)
                        total_markets_discovered += len(markets)

                        # Run predictions on predictable markets
                        for mkt in markets:
                            kai_market = mkt["market_type"]
                            if not market_is_predictable(kai_market):
                                continue

                            try:
                                result = self._engine.predict_with_odds(
                                    sport_key=kai_sport,
                                    market_type=kai_market,
                                    selection=mkt["selection"],
                                    home_team=home,
                                    away_team=away,
                                    bookmaker_odds=mkt["odds"],
                                    line=mkt.get("line"),
                                    bookmaker_name=mkt.get("bookmaker"),
                                )

                                total_predictions += 1

                                # Apply quality filters
                                if self._passes_quality(result, quality, live_data):
                                    qualified_count += 1

                                    # Store the qualified prediction
                                    pred_id = self._upsert_prediction_v3(
                                        db, event_id, sport_id, evt, result, mkt
                                    )

                                    # Build display output
                                    sportybet = sportybet_display(
                                        kai_market, result.selection, mkt.get("line")
                                    )

                                    all_qualified.append({
                                        "prediction_id": pred_id,
                                        "event_id": event_id,
                                        "sport": kai_sport,
                                        "sport_name": evt.get("sport", {}).get("name", kai_sport),
                                        "league": evt.get("league", {}).get("name", ""),
                                        "league_slug": evt.get("league", {}).get("slug", ""),
                                        "home": home,
                                        "away": away,
                                        "event_time": evt.get("date", ""),
                                        "market_type": kai_market,
                                        "market_name": mkt["market_name"],
                                        "selection": result.selection,
                                        "display_pick": sportybet,
                                        "odds": mkt["odds"],
                                        "bookmaker": mkt.get("bookmaker", ""),
                                        "probability": result.estimated_probability,
                                        "implied_probability": result.implied_probability,
                                        "edge": result.edge,
                                        "confidence": result.confidence,
                                        "risk_score": result.risk_score,
                                        "data_quality": result.data_quality,
                                        "prediction_id": pred_id,
                                    })

                            except Exception as e:
                                logger.warning(
                                    f"Prediction failed for {slug}/{event_id}/{mkt['market_type']}: {e}"
                                )
                                error_count += 1

                    except Exception as e:
                        logger.warning(f"Odds fetch failed for event {event_id}: {e}")
                        error_count += 1

            self._mark_refreshed(db, "last_odds_refresh")

        # Sort qualified picks by confidence * edge
        all_qualified.sort(
            key=lambda p: (p.get("confidence", 0) or 0) * max(0, (p.get("edge", 0) or 0)),
            reverse=True,
        )

        # Enforce uniqueness: one primary pick per event
        seen_events: Set[int] = set()
        unique_picks: List[Dict[str, Any]] = []
        other_markets: Dict[int, List[str]] = {}
        for pick in all_qualified:
            eid = pick["event_id"]
            if eid not in seen_events:
                seen_events.add(eid)
                unique_picks.append(pick)
            else:
                other_markets.setdefault(eid, []).append(pick["display_pick"])

        # Attach other qualified markets
        for pick in unique_picks:
            pick["other_markets"] = other_markets.get(pick["event_id"], [])

        logger.info(
            f"sync_odds_v3: {total_events_fetched} events, "
            f"{total_events_with_odds} with odds, "
            f"{total_markets_discovered} markets, "
            f"{total_predictions} predictions, "
            f"{qualified_count} qualified, "
            f"{len(unique_picks)} unique picks, "
            f"{error_count} errors"
        )

        return {
            "status": "ok",
            "events_fetched": total_events_fetched,
            "events_with_odds": total_events_with_odds,
            "markets_discovered": total_markets_discovered,
            "predictions_generated": total_predictions,
            "qualified_predictions": qualified_count,
            "unique_picks": len(unique_picks),
            "picks": unique_picks,
            "errors": error_count,
        }

    # ── Event Ingestion (v3 format) ──────────────────────────────────────────

    def _ingest_event_v3(self, db, sport_id: int, evt: Dict[str, Any],
                          home_name: str = None, away_name: str = None,
                          event_time: str = None) -> int:
        """Upsert event from Odds-API.io v3 format. Returns 1 if new, 0 if updated.

        home_name/away_name/event_time override evt's own fields when given.
        fetch_events()'s list response omits home/away/date for some sports
        (e.g. tennis); callers that already resolved these from the richer
        per-event fetch_odds() response should pass them through here too,
        instead of letting this fall back to evt's frequently-missing keys
        and silently writing placeholder "Home"/"Away" team names.

        When neither an override nor evt itself has real home/away/date data,
        and the event already exists, keep its current team links and
        event_time instead of overwriting them with the placeholder — this
        method (via refresh_events()) runs on its own periodic cycle
        independent of the odds sync that resolves real names, so without
        this it would silently revert an already-correctly-named event back
        to "Home"/"Away" the next time it ran without fresh odds data.
        """
        external_id = str(evt.get("id", ""))
        if not external_id:
            return 0

        resolved_home = home_name or evt.get("home")
        resolved_away = away_name or evt.get("away")
        resolved_time = event_time or evt.get("date")
        league_info = evt.get("league", {})
        league_slug = league_info.get("slug", "")
        league_name = league_info.get("name", league_slug)

        existing = db.execute(
            "SELECT id, home_team_id, away_team_id, event_time "
            "FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, external_id)
        ).fetchone()

        if resolved_home and resolved_away:
            home_id = upsert_team(db, sport_id, resolved_home)
            away_id = upsert_team(db, sport_id, resolved_away)
        elif existing:
            home_id = existing["home_team_id"]
            away_id = existing["away_team_id"]
        else:
            home_id = upsert_team(db, sport_id, "Home")
            away_id = upsert_team(db, sport_id, "Away")

        if resolved_time:
            final_time = resolved_time
        elif existing:
            final_time = existing["event_time"]
        else:
            final_time = ""

        # Upsert league (tier: 1-3 known division, 99 = unclassified/excluded)
        league_id = upsert_league(
            db, sport_id, league_slug, league_name,
            tier=_league_tier(league_name, league_slug) or 99,
        ) if league_slug else None

        upsert_event(
            db, sport_id, external_id,
            home_id, away_id, final_time,
            league_id=league_id,
            status=evt.get("status", "scheduled"),
        )

        return 1 if existing is None else 0

    # ── Legacy Results Sync ──────────────────────────────────────────────────

    def _sync_results_legacy(self) -> Dict[str, Any]:
        """Fetch scores using legacy provider."""
        from core.kai_betting.data_sources.odds_api import OddsAPISource, SPORT_KEY_MAP, kai_sport_for

        legacy = OddsAPISource()
        if not legacy.is_configured:
            return {"status": "skipped", "reason": "legacy ODDS_API_KEY not set"}
        if not legacy.has_quota():
            return {"status": "skipped", "reason": "legacy quota exhausted"}

        active_sports = self._get_active_sports()
        odds_keys: List[str] = []
        for kai_sport in active_sports:
            from core.kai_betting.data_sources.odds_api import odds_sport_keys_for
            odds_keys.extend(odds_sport_keys_for(kai_sport))

        if not odds_keys:
            return {"status": "skipped", "reason": "no legacy sport keys"}

        all_scores = legacy.fetch_scores(odds_keys, days_from=3)

        total_updated = 0
        total_settled = 0

        with get_db() as db:
            for odds_key, score_events in all_scores.items():
                kai_sport = kai_sport_for(odds_key)
                if not kai_sport:
                    continue

                sport_row = db.execute(
                    "SELECT id FROM sports WHERE key = ?", (kai_sport,)
                ).fetchone()
                if not sport_row:
                    continue
                sport_id = sport_row["id"]

                for se in score_events:
                    if not isinstance(se, dict):
                        continue
                    if not se.get("completed", False):
                        continue

                    ext_id = str(se.get("id", ""))
                    raw_scores = se.get("scores")
                    home_score = None
                    away_score = None

                    if isinstance(raw_scores, dict):
                        home_score = raw_scores.get("home_score")
                        away_score = raw_scores.get("away_score")
                    elif isinstance(raw_scores, list) and raw_scores:
                        home_team = se.get("home_team", "")
                        away_team = se.get("away_team", "")
                        for item in raw_scores:
                            if isinstance(item, dict):
                                if item.get("name") == home_team:
                                    home_score = item.get("score")
                                elif item.get("name") == away_team:
                                    away_score = item.get("score")

                    try:
                        home_score = int(home_score) if home_score is not None else None
                        away_score = int(away_score) if away_score is not None else None
                    except (ValueError, TypeError):
                        home_score = None
                        away_score = None

                    if home_score is None and away_score is None:
                        continue

                    cursor = db.execute("""
                        UPDATE events SET status = 'finished',
                        home_score = ?, away_score = ?, updated_at = datetime('now')
                        WHERE sport_id = ? AND external_id = ?
                    """, (home_score, away_score, sport_id, ext_id))
                    if cursor.rowcount > 0:
                        total_updated += 1

                    settled = self._settle_event_predictions(
                        db, sport_id, ext_id, home_score, away_score
                    )
                    total_settled += settled

            self._mark_refreshed(db, "last_results_refresh")

        logger.info(
            f"sync_results_legacy: {total_updated} events updated, {total_settled} settled"
        )
        return {
            "status": "ok",
            "events_updated": total_updated,
            "predictions_settled": total_settled,
        }

    def _sync_results_fallback_sgo(self) -> Dict[str, Any]:
        """Settle PRIMARY-provider events using SportsGameOdds' finalized
        results, when the legacy scores provider is unavailable or out of
        quota.

        SportsGameOdds and the primary provider (Odds-API.io / legacy
        odds_api) use incompatible external_id namespaces, so a finished
        SportsGameOdds event can't be looked up directly against the
        pending predictions' events — it's matched by team names + kickoff
        time instead (same fuzzy-match window used for ingest-time dedup,
        see _is_duplicate_event), then settled via the matched PRIMARY
        event's own sport_id/external_id.
        """
        from core.kai_betting.data_sources.sportsgameodds import (
            SportsGameOddsSource, league_ids_for, kai_sport_for_league,
            extract_result,
        )

        source = SportsGameOddsSource()
        if not source.is_configured:
            return {"status": "skipped", "reason": "SPORTSGAMEODDS_API_KEY not set"}

        if not self._should_run(
            "last_results_fallback_refresh",
            self._config_int("results_fallback_interval_minutes", 60) * 60,
        ):
            return {"status": "skipped", "reason": "within refresh interval"}

        active_sports = self._get_active_sports()
        league_ids: List[str] = []
        for kai_sport in active_sports:
            for lid in league_ids_for(kai_sport):
                if lid not in league_ids:
                    league_ids.append(lid)

        if not league_ids:
            with get_db() as db:
                self._mark_refreshed(db, "last_results_fallback_refresh")
            return {"status": "skipped", "reason": "no matching leagues for active sports"}

        # No odds needed for settlement — finished games often drop odds
        # availability, so don't filter on it; just ask for finalized games.
        # startsAfter is required: without it SGO returns every finalized
        # event it has ever recorded (years back), none of which can match
        # a currently-pending prediction. 10 days covers the longest a
        # prediction's event could sit un-settled (events are ingested up
        # to 7 days ahead of kickoff).
        lookback = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        events = source.fetch_events(
            league_ids, odds_available=False, finalized=True, starts_after=lookback,
        )

        total_matched = 0
        total_settled = 0
        error_count = 0

        with get_db() as db:
            for evt in events:
                try:
                    result = extract_result(evt)
                    if not result:
                        continue

                    kai_sport = kai_sport_for_league(evt.get("leagueID", ""))
                    if not kai_sport:
                        continue
                    sport_row = db.execute(
                        "SELECT id FROM sports WHERE key = ?", (kai_sport,)
                    ).fetchone()
                    if not sport_row:
                        continue
                    sport_id = sport_row["id"]

                    teams = evt.get("teams", {})
                    home_name = teams.get("home", {}).get("names", {}).get("long", "")
                    away_name = teams.get("away", {}).get("names", {}).get("long", "")
                    event_time = evt.get("status", {}).get("startsAt", "")
                    if not home_name or not away_name:
                        continue

                    matched = self._find_matching_event(
                        db, sport_id, home_name, away_name, event_time
                    )
                    if not matched:
                        continue
                    total_matched += 1

                    db.execute("""
                        UPDATE events SET status = 'finished',
                        home_score = ?, away_score = ?, updated_at = datetime('now')
                        WHERE id = ?
                    """, (result["home_score"], result["away_score"], matched["id"]))

                    total_settled += self._settle_event_predictions(
                        db, sport_id, matched["external_id"],
                        result["home_score"], result["away_score"],
                    )
                except Exception as e:
                    logger.warning(f"results fallback (sportsgameodds) failed for event: {e}")
                    error_count += 1

            self._mark_refreshed(db, "last_results_fallback_refresh")

        logger.info(
            f"sync_results_fallback_sgo: {total_matched} matched, "
            f"{total_settled} settled, {error_count} errors"
        )
        return {
            "status": "ok",
            "events_matched": total_matched,
            "predictions_settled": total_settled,
            "errors": error_count,
        }

    # ── SportsGameOdds Sync (supplemental, independent pipeline) ─────────────
    # SportsGameOdds events are ingested as their own event rows (external_id
    # = SportsGameOdds' own eventID) — never reconciled against odds_api_io/
    # legacy rows, since those providers use incompatible ID schemes with no
    # shared key. A team-name + kickoff-time dedup guard below prevents
    # duplicate Telegram picks for the same real-world match; otherwise this
    # is purely additive coverage.

    def _sync_sportsgameodds(self) -> Dict[str, Any]:
        """Supplemental sync from SportsGameOdds.

        Independent of the primary provider: own API key, own quota, own
        sync interval (default 4x/day — quota discipline, not every 300s
        cycle). Ingests events+odds+results in a single call per league
        batch, runs qualified markets through PredictionEngine, and settles
        finished events using the same helpers the legacy pipeline uses.
        """
        from core.kai_betting.data_sources.sportsgameodds import (
            SportsGameOddsSource, league_ids_for, kai_sport_for_league,
            extract_markets, extract_result,
        )
        from core.kai_betting.data_sources.odds_api_io import market_is_predictable

        source = SportsGameOddsSource()
        if not source.is_configured:
            return {"status": "skipped", "reason": "SPORTSGAMEODDS_API_KEY not set"}

        if not self._should_run(
            "last_sportsgameodds_refresh",
            self._config_int("sportsgameodds_interval_minutes", 360) * 60,
        ):
            return {"status": "skipped", "reason": "within refresh interval"}

        active_sports = self._get_active_sports()
        league_ids: List[str] = []
        for kai_sport in active_sports:
            for lid in league_ids_for(kai_sport):
                if lid not in league_ids:
                    league_ids.append(lid)

        if not league_ids:
            with get_db() as db:
                self._mark_refreshed(db, "last_sportsgameodds_refresh")
            return {"status": "skipped", "reason": "no matching leagues for active sports"}

        events = source.fetch_events(league_ids, odds_available=True)

        total_new = 0
        total_updated = 0
        total_dedup_skipped = 0
        total_settled = 0
        predictions_generated = 0
        qualified_count = 0
        error_count = 0

        with get_db() as db:
            live_data = _is_live_data_mode(db)
            quality = self._load_quality_config(db)

            for evt in events:
                try:
                    kai_sport = kai_sport_for_league(evt.get("leagueID", ""))
                    if not kai_sport:
                        continue

                    sport_row = db.execute(
                        "SELECT id FROM sports WHERE key = ?", (kai_sport,)
                    ).fetchone()
                    if not sport_row:
                        continue
                    sport_id = sport_row["id"]

                    ext_id = str(evt.get("eventID", ""))
                    if not ext_id:
                        continue

                    teams = evt.get("teams", {})
                    home_name = teams.get("home", {}).get("names", {}).get("long", "Home")
                    away_name = teams.get("away", {}).get("names", {}).get("long", "Away")
                    event_time = evt.get("status", {}).get("startsAt", "")

                    # Skip if this event already exists under a different
                    # provider's namespace (same teams, kickoff within ±3h).
                    existing_id = db.execute(
                        "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
                        (sport_id, ext_id)
                    ).fetchone()
                    if not existing_id and self._is_duplicate_event(
                        db, sport_id, home_name, away_name, event_time
                    ):
                        total_dedup_skipped += 1
                        continue

                    # _league_tier() keywords are space-separated (e.g.
                    # "champions league") but SportsGameOdds leagueIDs use
                    # underscores ("UEFA_CHAMPIONS_LEAGUE") — normalize or
                    # every SGO league silently resolves to tier 99/excluded.
                    league_id_raw = evt.get("leagueID", kai_sport)
                    league_name = league_id_raw.replace("_", " ").title()
                    is_new = self._ingest_event_sgo(
                        db, sport_id, ext_id, home_name, away_name,
                        event_time, league_id_raw, league_name,
                    )
                    if is_new:
                        total_new += 1
                    else:
                        total_updated += 1

                    # Settlement for finished events — same helper the
                    # legacy pipeline uses, since results are keyed by this
                    # same external_id (no fuzzy matching needed).
                    result = extract_result(evt)
                    if result:
                        db.execute("""
                            UPDATE events SET status = 'finished',
                            home_score = ?, away_score = ?, updated_at = datetime('now')
                            WHERE sport_id = ? AND external_id = ?
                        """, (result["home_score"], result["away_score"], sport_id, ext_id))
                        total_settled += self._settle_event_predictions(
                            db, sport_id, ext_id, result["home_score"], result["away_score"]
                        )
                        continue  # finished events don't need fresh predictions
                    if evt.get("status", {}).get("finalized"):
                        continue  # finalized but no usable score — skip predictions too

                    # Generate predictions from qualifying markets
                    markets = extract_markets(evt)
                    for mkt in markets:
                        kai_market = mkt["market_type"]
                        if not market_is_predictable(kai_market):
                            continue
                        try:
                            result = self._engine.predict_with_odds(
                                sport_key=kai_sport,
                                market_type=kai_market,
                                selection=mkt["selection"],
                                home_team=home_name,
                                away_team=away_name,
                                bookmaker_odds=mkt["odds"],
                                line=mkt.get("line"),
                                bookmaker_name=mkt.get("bookmaker"),
                            )
                            predictions_generated += 1
                            if self._passes_quality(result, quality, live_data):
                                qualified_count += 1
                                self._upsert_prediction_v3(
                                    db, ext_id, sport_id,
                                    {"id": ext_id}, result, mkt,
                                )
                        except Exception as e:
                            logger.warning(
                                f"sportsgameodds prediction failed for {ext_id}/{kai_market}: {e}"
                            )
                            error_count += 1

                except Exception as e:
                    logger.warning(f"sportsgameodds ingest failed for event: {e}")
                    error_count += 1

            self._mark_refreshed(db, "last_sportsgameodds_refresh")

        logger.info(
            f"sync_sportsgameodds: {total_new} new, {total_updated} updated, "
            f"{total_dedup_skipped} dedup-skipped, {total_settled} settled, "
            f"{predictions_generated} predictions, {qualified_count} qualified, "
            f"{error_count} errors"
        )
        return {
            "status": "ok",
            "new_events": total_new,
            "updated_events": total_updated,
            "dedup_skipped": total_dedup_skipped,
            "predictions_settled": total_settled,
            "predictions_generated": predictions_generated,
            "qualified_predictions": qualified_count,
            "errors": error_count,
        }

    def _ingest_event_sgo(self, db, sport_id: int, ext_id: str,
                          home_name: str, away_name: str,
                          event_time: str, league_id_raw: str,
                          league_name: str) -> bool:
        """Upsert a SportsGameOdds event. Returns True if newly created."""
        home_id = upsert_team(db, sport_id, home_name)
        away_id = upsert_team(db, sport_id, away_name)

        league_id = upsert_league(
            db, sport_id, f"sgo_{league_id_raw.lower()}", league_name,
            tier=_league_tier(league_name) or 99,
        )

        existing = db.execute(
            "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, ext_id)
        ).fetchone()

        upsert_event(
            db, sport_id, ext_id,
            home_id, away_id, event_time,
            league_id=league_id,
            status="scheduled",
        )

        return existing is None

    def _is_duplicate_event(self, db, sport_id: int, home_name: str,
                            away_name: str, event_time: str) -> bool:
        """Check for an existing event (any provider) with matching team
        names (case-insensitive) and kickoff within a +/-3 hour window —
        guards against duplicate Telegram picks for the same real-world
        match when more than one provider covers it.
        """
        if not event_time:
            return False
        try:
            target = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return False

        candidates = db.execute("""
            SELECT e.event_time FROM events e
            JOIN teams h ON e.home_team_id = h.id
            JOIN teams a ON e.away_team_id = a.id
            WHERE e.sport_id = ? AND LOWER(h.name) = LOWER(?) AND LOWER(a.name) = LOWER(?)
        """, (sport_id, home_name, away_name)).fetchall()

        for row in candidates:
            try:
                candidate_time = datetime.fromisoformat(
                    (row["event_time"] or "").replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                continue
            if abs((candidate_time - target).total_seconds()) <= 3 * 3600:
                return True
        return False

    @staticmethod
    def _normalize_team_name(name: str) -> str:
        """Fold a team name to a provider-agnostic comparison key.

        Different providers spell the same club differently (accents,
        periods, abbreviation punctuation) — e.g. "CF Montreal" vs
        "CF Montréal", "D.C. United" vs "DC United". Strips diacritics
        and punctuation so those collapse to the same key. Deliberately
        does NOT strip suffixes like "SC"/"FC"/"B" — that would conflate
        distinct clubs (e.g. "Orlando City SC" vs its reserve side
        "Orlando City B").
        """
        if not name:
            return ""
        folded = unicodedata.normalize("NFKD", name)
        folded = "".join(c for c in folded if not unicodedata.combining(c))
        folded = folded.lower()
        folded = re.sub(r"[.'’]", "", folded)
        folded = re.sub(r"\s+", " ", folded).strip()
        return folded

    def _find_matching_event(self, db, sport_id: int, home_name: str,
                             away_name: str, event_time: str):
        """Find an existing, not-yet-finished event (any provider) with
        matching team names and kickoff within a +/-3 hour window. Returns
        the matched row (id, external_id) or None.

        Team names are compared via _normalize_team_name rather than exact
        LOWER() equality, since providers spell the same club differently.

        Used to settle a PRIMARY-provider event using a supplemental
        provider's result, since providers use incompatible external_id
        namespaces — see _sync_results_fallback_sgo. Excludes already-
        'finished' events so this doesn't repeatedly re-match (and
        redundantly re-settle) events a provider's own native pipeline
        already closed out.
        """
        if not event_time:
            return None
        try:
            target = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

        target_home = self._normalize_team_name(home_name)
        target_away = self._normalize_team_name(away_name)
        if not target_home or not target_away:
            return None

        candidates = db.execute("""
            SELECT e.id, e.external_id, e.event_time, h.name home_name, a.name away_name
            FROM events e
            JOIN teams h ON e.home_team_id = h.id
            JOIN teams a ON e.away_team_id = a.id
            WHERE e.sport_id = ? AND e.status != 'finished'
        """, (sport_id,)).fetchall()

        for row in candidates:
            if self._normalize_team_name(row["home_name"]) != target_home:
                continue
            if self._normalize_team_name(row["away_name"]) != target_away:
                continue
            try:
                candidate_time = datetime.fromisoformat(
                    (row["event_time"] or "").replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                continue
            if abs((candidate_time - target).total_seconds()) <= 3 * 3600:
                return row
        return None

    # ── Quality Filters ──────────────────────────────────────────────────────

    def _load_quality_config(self, db) -> Dict[str, Any]:
        """Load quality thresholds from config, falling back to defaults."""
        cfg = dict(DEFAULT_QUALITY)
        for key in cfg:
            try:
                row = db.execute(
                    "SELECT value FROM betting_config WHERE key = ?",
                    (f"quality_{key}",)
                ).fetchone()
                if row and row["value"]:
                    if isinstance(cfg[key], bool):
                        cfg[key] = row["value"] == "true"
                    else:
                        cfg[key] = float(row["value"])
            except Exception:
                pass
        return cfg

    def _passes_quality(self, result, quality: Dict[str, Any],
                        live_data: bool) -> bool:
        """Check if a prediction passes all quality filters."""
        # In LIVE_DATA_MODE, reject anything without real odds
        if live_data and not result.bookmaker_odds:
            return False

        if result.estimated_probability < quality["min_probability"]:
            return False
        if result.confidence < quality["min_confidence"]:
            return False
        if result.data_quality < quality["min_data_quality"]:
            return False
        if quality["require_positive_edge"] and (result.edge is None or result.edge <= 0):
            return False
        if quality["require_real_odds"] and not result.bookmaker_odds:
            return False

        return True

    # ── Prediction Storage ───────────────────────────────────────────────────

    def _upsert_prediction_v3(self, db, event_id: int, sport_id: int,
                              evt: Dict[str, Any], result,
                              mkt: Dict[str, Any]) -> int:
        """Store a prediction with all event/odds context."""
        # Find the DB event row
        ext_id = str(evt.get("id", event_id))
        event_row = db.execute(
            "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, ext_id)
        ).fetchone()
        db_event_id = event_row["id"] if event_row else None

        line = mkt.get("line")
        # SportyBet-friendly market name — includes the line for over/under
        # (e.g. "Over 2.5 Goals") so users can find the exact market.
        market_name = sportybet_display(result.market_type, result.selection, line)

        # Check for existing prediction (dedup by event + market + selection + line)
        if db_event_id:
            existing = db.execute("""
                SELECT id FROM predictions
                WHERE event_id = ? AND market_type = ? AND selection = ? AND line IS ?
                LIMIT 1
            """, (db_event_id, result.market_type, result.selection, line)).fetchone()

            if existing:
                db.execute("""
                    UPDATE predictions SET
                        market_name = ?, line = ?,
                        bookmaker_odds = ?, estimated_probability = ?,
                        implied_probability = ?, edge = ?,
                        confidence = ?, risk_score = ?, data_quality = ?,
                        reasoning = ?, tags = ?, status = 'published',
                        data_timestamp = datetime('now'), updated_at = datetime('now')
                    WHERE id = ?
                """, (
                    market_name, line,
                    result.bookmaker_odds, result.estimated_probability,
                    result.implied_probability, result.edge,
                    result.confidence, result.risk_score, result.data_quality,
                    result.reasoning, ",".join(result.tags),
                    existing["id"],
                ))
                return existing["id"]

        # Insert new
        cursor = db.execute("""
            INSERT INTO predictions (
                event_id, sport_id, market_type, market_name, selection,
                bookmaker_odds, line, estimated_probability, implied_probability, edge,
                confidence, risk_score, data_quality, reasoning, tags,
                correlation_group, model_version, status, data_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', datetime('now'))
        """, (
            db_event_id, sport_id,
            result.market_type, market_name, result.selection,
            result.bookmaker_odds, line, result.estimated_probability,
            result.implied_probability, result.edge,
            result.confidence, result.risk_score, result.data_quality,
            result.reasoning, ",".join(result.tags),
            result.correlation_group, result.model_version,
        ))
        return cursor.lastrowid

    # ── Config helpers ───────────────────────────────────────────────────────

    def _should_run(self, config_key: str, interval_seconds: int) -> bool:
        """Check if enough time has passed since the last run."""
        with get_db() as db:
            row = db.execute(
                "SELECT value FROM betting_config WHERE key = ?", (config_key,)
            ).fetchone()
            if not row or not row["value"]:
                return True

            try:
                last = datetime.fromisoformat(row["value"])
                elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                return elapsed >= interval_seconds
            except (ValueError, OSError):
                return True

    def _mark_refreshed(self, db, config_key: str):
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT OR REPLACE INTO betting_config (key, value) VALUES (?, ?)",
            (config_key, now),
        )

    def _config_int(self, key: str, default: int) -> int:
        try:
            with get_db() as db:
                row = db.execute(
                    "SELECT value FROM betting_config WHERE key = ?", (key,)
                ).fetchone()
                if row and row["value"]:
                    return int(row["value"])
        except Exception:
            pass
        return default

    def _config_bool(self, key: str, default: bool) -> bool:
        try:
            with get_db() as db:
                row = db.execute(
                    "SELECT value FROM betting_config WHERE key = ?", (key,)
                ).fetchone()
                if row and row["value"]:
                    return row["value"].strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            pass
        return default

    def _get_active_sports(self) -> List[str]:
        with get_db() as db:
            row = db.execute(
                "SELECT value FROM betting_config WHERE key = 'active_sports_for_sync'"
            ).fetchone()
            if row and row["value"]:
                return [s.strip() for s in row["value"].split(",") if s.strip()]
        return ["football", "basketball", "tennis"]

    def _sports_to_slugs(self, kai_sports: List[str]) -> List[str]:
        """Convert Kai sport keys to Odds-API.io slugs."""
        slugs = []
        for kai in kai_sports:
            slug = slug_for_kai(kai)
            if slug:
                slugs.append(slug)
        return slugs

    def _settle_event_predictions(self, db, sport_id, ext_id,
                                  home_score, away_score) -> int:
        """Settle predictions for a finished event."""
        from core.kai_betting.workers import KaiBettingWorkers

        event_row = db.execute(
            "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, ext_id)
        ).fetchone()
        if not event_row:
            return 0
        event_id = event_row["id"]

        rows = db.execute("""
            SELECT p.id, p.selection, p.market_type
            FROM predictions p
            WHERE p.event_id = ? AND p.status IN ('pending','published','quality_check','approved')
        """, (event_id,)).fetchall()

        settled = 0
        for row in rows:
            outcome = KaiBettingWorkers._determine_outcome(
                row["market_type"], row["selection"],
                home_score or 0, away_score or 0,
            )
            if outcome:
                db.execute("""
                    INSERT OR REPLACE INTO prediction_results
                    (prediction_id, outcome, actual_score_home, actual_score_away, settled_by)
                    VALUES (?, ?, ?, ?, 'auto')
                """, (row["id"], outcome, home_score, away_score))
                db.execute("""
                    UPDATE predictions SET status = ?, settled_at = datetime('now'), updated_at = datetime('now')
                    WHERE id = ?
                """, (outcome, row["id"]))
                settled += 1

        return settled
