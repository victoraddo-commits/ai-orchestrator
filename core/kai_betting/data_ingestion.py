"""Kai Betting — Data Ingestion Manager (Odds-API.io v3).

Orchestrates the real-data pipeline:
  Odds-API.io → Real Events → Real Odds → All Markets → Predictions → Quality Filter → Store

Uses OddsAPIioSource as primary. Falls back to legacy OddsAPISource if unavailable.
With LIVE_DATA_MODE=true, synthetic/placeholder data is rejected.
"""

from __future__ import annotations

import logging
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
                        count = self._ingest_event(db, sport_id, slug, evt)
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
        if not self.is_configured:
            return {"status": "skipped", "reason": "ODDS_API_IO_KEY not set"}

        odds_result = {"status": "skipped", "reason": "within refresh interval"}
        results_result = {"status": "skipped", "reason": "within refresh interval"}

        if with_odds and self._should_run("last_odds_refresh",
                                          self._config_int("odds_interval_minutes", 60) * 60):
            odds_result = self._sync_odds_v3()

        # Results sync still uses the legacy provider for now
        if with_results and self._should_run("last_results_refresh",
                                             self._config_int("results_interval_minutes", 30) * 60):
            try:
                results_result = self._sync_results_legacy()
            except Exception as e:
                logger.error(f"_sync_results failed: {e}", exc_info=True)
                results_result = {"status": "error", "error": str(e)}

        return {
            "odds": odds_result,
            "results": results_result,
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

                        # Upsert event into DB
                        self._ingest_event_v3(db, sport_id, evt)

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

    def _ingest_event_v3(self, db, sport_id: int, evt: Dict[str, Any]) -> int:
        """Upsert event from Odds-API.io v3 format. Returns 1 if new, 0 if updated."""
        external_id = str(evt.get("id", ""))
        if not external_id:
            return 0

        home_name = evt.get("home", "Home")
        away_name = evt.get("away", "Away")
        event_time = evt.get("date", "")
        league_info = evt.get("league", {})
        league_slug = league_info.get("slug", "")
        league_name = league_info.get("name", league_slug)

        # Upsert teams
        home_id = upsert_team(db, sport_id, home_name)
        away_id = upsert_team(db, sport_id, away_name)

        # Upsert league
        league_id = upsert_league(db, sport_id, league_slug, league_name) if league_slug else None

        existing = db.execute(
            "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, external_id)
        ).fetchone()

        upsert_event(
            db, sport_id, external_id,
            home_id, away_id, event_time,
            league_id=league_id,
            status=evt.get("status", "scheduled"),
        )

        return 1 if existing is None else 0

    # ── Legacy Event Ingestion ───────────────────────────────────────────────

    def _ingest_event(self, db, sport_id: int, odds_key: str,
                      evt: Dict[str, Any]) -> int:
        """Upsert an event from The Odds API v4 format. Returns 1 if new."""
        external_id = str(evt.get("id", ""))
        if not external_id:
            return 0

        home_name = evt.get("home_team", "Home")
        away_name = evt.get("away_team", "Away")
        event_time = evt.get("commence_time", "")

        home_id = upsert_team(db, sport_id, home_name)
        away_id = upsert_team(db, sport_id, away_name)

        league_key = odds_key.split("_", 1)[-1] if "_" in odds_key else odds_key
        league_name = league_key.replace("_", " ").title()

        existing = db.execute(
            "SELECT id FROM events WHERE sport_id = ? AND external_id = ?",
            (sport_id, external_id)
        ).fetchone()

        league_id = upsert_league(db, sport_id, league_key, league_name)

        upsert_event(
            db, sport_id, external_id,
            home_id, away_id, event_time,
            league_id=league_id,
            status="scheduled",
        )

        return 1 if existing is None else 0

    # ── Legacy Results Sync ──────────────────────────────────────────────────

    def _sync_results_legacy(self) -> Dict[str, Any]:
        """Fetch scores using legacy provider."""
        from core.kai_betting.data_sources.odds_api import OddsAPISource, SPORT_KEY_MAP, kai_sport_for

        legacy = OddsAPISource()
        if not legacy.is_configured:
            return {"status": "skipped", "reason": "legacy ODDS_API_KEY not set"}

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

        # Check for existing prediction (dedup by event + market + selection)
        if db_event_id:
            existing = db.execute("""
                SELECT id FROM predictions
                WHERE event_id = ? AND market_type = ? AND selection = ?
                LIMIT 1
            """, (db_event_id, result.market_type, result.selection)).fetchone()

            if existing:
                db.execute("""
                    UPDATE predictions SET
                        bookmaker_odds = ?, estimated_probability = ?,
                        implied_probability = ?, edge = ?,
                        confidence = ?, risk_score = ?, data_quality = ?,
                        reasoning = ?, tags = ?, status = 'published',
                        data_timestamp = datetime('now'), updated_at = datetime('now')
                    WHERE id = ?
                """, (
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
                bookmaker_odds, estimated_probability, implied_probability, edge,
                confidence, risk_score, data_quality, reasoning, tags,
                correlation_group, model_version, status, data_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', datetime('now'))
        """, (
            db_event_id, sport_id,
            result.market_type, result.market_name, result.selection,
            result.bookmaker_odds, result.estimated_probability,
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
