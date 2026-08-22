"""Covers the one-time scope migration deactivating out-of-scope data."""

from core.kai_betting.scope_migration import apply_scope
from core.kai_betting.db import get_db, upsert_league


def _seed_leagues(db):
    football_id = db.execute("SELECT id FROM sports WHERE key = 'football'").fetchone()["id"]
    tennis_id = db.execute("SELECT id FROM sports WHERE key = 'tennis'").fetchone()["id"]
    # Approved
    upsert_league(db, football_id, "epl", "Premier League")
    upsert_league(db, football_id, "italy-serie-a", "Italy Serie A")
    # Out of scope
    upsert_league(db, football_id, "usa-mls", "USA MLS")
    upsert_league(db, tennis_id, "itf-tianjin", "ITF Tianjin")


def test_apply_scope_disables_out_of_scope_sports_and_leagues(fresh_db):
    with get_db() as db:
        _seed_leagues(db)
        result = apply_scope(db)

        # Approved sports stay active; everything else is deactivated.
        active_sports = {
            r["key"] for r in db.execute("SELECT key FROM sports WHERE is_active = 1")
        }
        assert active_sports == {"football", "tennis", "basketball"}

        # Approved leagues remain active; out-of-scope leagues are deactivated.
        def active(key):
            return db.execute(
                "SELECT is_active FROM leagues WHERE key = ?", (key,)
            ).fetchone()["is_active"]

        assert active("epl") == 1
        assert active("italy-serie-a") == 1
        assert active("usa-mls") == 0
        assert active("itf-tianjin") == 0

        assert result["sports_disabled"] == 7  # the 7 non-approved SPORT_SEEDS
        assert result["leagues_disabled"] == 2


def test_apply_scope_reactivates_approved_leagues(fresh_db):
    """An approved league that was previously deactivated is re-activated."""
    with get_db() as db:
        _seed_leagues(db)
        # Simulate a prior deactivation (e.g. an earlier, stricter run).
        db.execute("UPDATE leagues SET is_active = 0 WHERE key = 'epl'")
        db.execute("UPDATE leagues SET is_active = 0 WHERE key = 'italy-serie-a'")
        db.commit()

        result = apply_scope(db)

        assert result["leagues_activated"] == 2  # epl + italy-serie-a
        assert db.execute(
            "SELECT is_active FROM leagues WHERE key = 'epl'"
        ).fetchone()["is_active"] == 1
        assert db.execute(
            "SELECT is_active FROM leagues WHERE key = 'italy-serie-a'"
        ).fetchone()["is_active"] == 1
