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
    """Sync the DB to the approved scope (idempotent).

    Activates approved sports/leagues and deactivates everything else, so a
    later widening/tightening of the whitelist can be re-applied. Returns a
    summary.
    """
    sports = tuple(sorted(scope.APPROVED_SPORTS))
    placeholders = ", ".join("?" * len(sports))
    conn.execute(
        f"UPDATE sports SET is_active = 0 WHERE key NOT IN ({placeholders})",
        sports,
    )
    conn.execute(
        f"UPDATE sports SET is_active = 1 WHERE key IN ({placeholders})",
        sports,
    )

    leagues = conn.execute(
        "SELECT l.id, l.key, l.name, s.key AS sport_key "
        "FROM leagues l JOIN sports s ON s.id = l.sport_id"
    ).fetchall()

    activated = 0
    disabled = 0
    for row in leagues:
        c = scope.classify_competition(row["sport_key"], row["name"], row["key"])
        new_active = 1 if c.allowed else 0
        current = conn.execute(
            "SELECT is_active FROM leagues WHERE id = ?", (row["id"],)
        ).fetchone()["is_active"]
        if current != new_active:
            conn.execute(
                "UPDATE leagues SET is_active = ? WHERE id = ?",
                (new_active, row["id"]),
            )
            if new_active:
                activated += 1
            else:
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
        "leagues_activated": activated,
        "leagues_disabled": disabled,
        "leagues_remaining_active": leagues_active,
    }
