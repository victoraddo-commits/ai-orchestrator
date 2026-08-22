"""One-time scope migration: archive (deactivate) everything outside the
approved sports/competition universe.

Does NOT delete data — it only flips is_active=0 so the API/dashboard no
longer surface out-of-scope sports/leagues and they can never feed the
prediction pipeline. Historical rows are preserved for reporting/audit.

The ingestion filter in scope.py is the hard enforcement; this migration is
belt-and-braces so a pre-existing DB (seeded before the scope reset) is
brought into line too.
"""

from __future__ import annotations

from typing import Dict, Any

from core.kai_betting import scope


def apply_scope(conn) -> Dict[str, Any]:
    """Deactivate out-of-scope sports and leagues. Returns a summary."""
    sports = tuple(sorted(scope.APPROVED_SPORTS))
    conn.execute(
        "UPDATE sports SET is_active = 0 WHERE key NOT IN (%s)"
        % ", ".join("?" * len(sports)),
        sports,
    )

    leagues = conn.execute(
        "SELECT l.id, l.key, l.name, s.key AS sport_key "
        "FROM leagues l JOIN sports s ON s.id = l.sport_id"
    ).fetchall()

    disabled = 0
    for row in leagues:
        c = scope.classify_competition(row["sport_key"], row["name"], row["key"])
        if not c.allowed:
            conn.execute("UPDATE leagues SET is_active = 0 WHERE id = ?", (row["id"],))
            disabled += 1

    conn.commit()

    sports_disabled = conn.execute(
        "SELECT COUNT(*) AS c FROM sports WHERE is_active = 0"
    ).fetchone()["c"]
    leagues_active = conn.execute(
        "SELECT COUNT(*) AS c FROM leagues WHERE is_active = 1"
    ).fetchone()["c"]

    return {
        "sports_disabled": sports_disabled,
        "leagues_disabled": disabled,
        "leagues_remaining_active": leagues_active,
    }
