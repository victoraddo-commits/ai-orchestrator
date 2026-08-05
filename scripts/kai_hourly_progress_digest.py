"""Hourly Kai Telegram digest of roadmap, susu module, and legal module progress.

This script consolidates progress reports from:
1. Roadmap - current phases, completed count, pending items
2. SUSU module - user registrations, groups, contributions
3. Law Tutor module - student progress, topics studied

The script runs via cron every hour and uses Omniroute for any AI delegation
if additional reasoning or summarization is needed.

Usage: .venv/bin/python scripts/kai_hourly_progress_digest.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.ai.ai_router as ai_router
import core.telegram_bridge as telegram_bridge
import core.roadmap_engine as roadmap_engine
import core.memory as memory


def get_roadmap_progress():
    """Get roadmap completion status."""
    roadmap = roadmap_engine.load_roadmap()
    phases = roadmap.get("phases", [])

    completed = len([p for p in phases if p.get("status") == "completed"])
    in_progress = len([p for p in phases if p.get("status") == "in_progress"])
    pending = len([p for p in phases if p.get("status") == "pending"])
    failed = len([p for p in phases if p.get("status") == "failed"])
    total = len(phases)

    # Get the top 3 pending phases by priority
    pending_phases = sorted(
        [p for p in phases if p.get("status") == "pending"],
        key=lambda p: p.get("priority", 999)
    )[:3]

    return {
        "completed": completed,
        "in_progress": in_progress,
        "pending": pending,
        "failed": failed,
        "total": total,
        "percentage": round((completed / total * 100), 1) if total > 0 else 0,
        "pending_phases": pending_phases
    }


def get_susu_progress():
    """Get SUSU module progress status."""
    try:
        # Try to get SUSU database stats
        import sqlite3
        from pathlib import Path

        db_path = Path.home() / ".susu" / "susu.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM members")
            total_members = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM susu_groups")
            total_groups = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM contributions")
            total_contributions = cursor.fetchone()[0]

            cursor.execute("SELECT SUM(amount) FROM contributions WHERE status='paid'")
            total_volume = cursor.fetchone()[0] or 0

            conn.close()

            return {
                "active": True,
                "total_members": total_members,
                "total_groups": total_groups,
                "total_contributions": total_contributions,
                "total_volume": float(total_volume)
            }
        else:
            return {"active": False, "reason": "Database not found"}
    except Exception as e:
        return {"active": False, "reason": str(e)}


def get_legal_progress():
    """Get Law Tutor module progress status."""
    try:
        from core.law_tutor import session as law_session

        # Get stats from session memory
        sessions = memory.load("law_tutor_sessions.json")
        if isinstance(sessions, dict):
            sessions = sessions.get("records", [])

            topics = set()
            total_queries = 0
            for s in sessions:
                total_queries += 1
                if "topic" in s:
                    topics.add(s["topic"])

            return {
                "active": True,
                "total_sessions": len(sessions),
                "unique_topics": len(topics),
                "topics_covered": list(topics)[:10]
            }
        else:
            return {"active": False, "reason": "No session data"}
    except Exception as e:
        return {"active": False, "reason": str(e)}


def format_progress_message():
    """Format the progress report message."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(hours=24)

    roadmap = get_roadmap_progress()
    susu = get_susu_progress()
    legal = get_legal_progress()

    lines = []

    # Header
    lines.append(f"📊 *Kai Hourly Progress Report*")
    lines.append(f"_{now.strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")

    # Roadmap Progress
    lines.append(f"📈 *Roadmap Progress*")
    lines.append(f"  Completed: {roadmap['completed']}/{roadmap['total']} ({roadmap['percentage']}%)")
    lines.append(f"  In Progress: {roadmap['in_progress']}")
    lines.append(f"  Pending: {roadmap['pending']}")
    lines.append(f"  Failed: {roadmap['failed']}")

    if roadmap['pending_phases']:
        lines.append("  Top Pending:")
        for p in roadmap['pending_phases']:
            lines.append(f"    • {p['id']}: {p.get('name', 'Unknown')[:50]}")

    lines.append("")

    # SUSU Progress
    if susu.get("active"):
        lines.append(f"💰 *SUSU Module*")
        lines.append(f"  Members: {susu['total_members']}")
        lines.append(f"  Groups: {susu['total_groups']}")
        lines.append(f"  Contributions: {susu['total_contributions']}")
        lines.append(f"  Volume: ${susu['total_volume']:,.2f}")
    else:
        lines.append(f"💰 *SUSU Module*")
        lines.append(f"  Status: Not active - {susu.get('reason', 'Unknown')}")
    lines.append("")

    # Legal Module Progress
    if legal.get("active"):
        lines.append(f"⚖️ *Law Tutor Module*")
        lines.append(f"  Sessions: {legal['total_sessions']}")
        lines.append(f"  Topics: {legal['unique_topics']}")
        if legal.get("topics_covered"):
            topics_str = ", ".join(legal["topics_covered"][:5])
            lines.append(f"  Topics: {topics_str}")
    else:
        lines.append(f"⚖️ *Law Tutor Module*")
        lines.append(f"  Status: Not active - {legal.get('reason', 'Unknown')}")
    lines.append("")

    # Footer
    lines.append(f"🤖 Omniroute delegated this report")
    lines.append(f"Next update in ~1 hour")

    return "\n".join(lines)


def main():
    """Main entry point."""
    message = format_progress_message()

    # Send via Telegram
    try:
        telegram_bridge.send_message(message)
        print(f"[{datetime.now(timezone.utc).isoformat()}] Progress report sent to Telegram")
        return 0
    except Exception as e:
        print(f"[{datetime.now(timezone.utc).isoformat()}] Failed to send progress report: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
