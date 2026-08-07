"""
KLAUS Autonomous Legal Knowledge Acquisition — Launch & Monitor

Starts the KLAUS scheduler + background workers, triggers an immediate
acquisition cycle, and sends Telegram progress updates.
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.klaus.scheduler import start_scheduler, trigger_job_now, _ensure_seeds
from core.klaus.db_manager import list_sources, get_documents_flagged_for_review, get_cursor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("klaus.launch")

def telegram(msg: str):
    """Send a Telegram update via the bridge."""
    try:
        from core.telegram_bridge import send_message
        send_message(f"📚 KLAUS: {msg}")
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")

def get_store_stats() -> dict:
    """Get current storage statistics."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT COUNT(*) as ct FROM klaus_sources WHERE status='active'")
            active_sources = cur.fetchone()["ct"]
            cur.execute("SELECT COUNT(*) as ct FROM klaus_documents")
            total_docs = cur.fetchone()["ct"]
            cur.execute("""SELECT review_status, COUNT(*) as ct FROM klaus_documents
                          GROUP BY review_status""")
            statuses = {r["review_status"]: r["ct"] for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) as ct FROM klaus_audit_logs")
            audit_entries = cur.fetchone()["ct"]
        return {
            "active_sources": active_sources,
            "total_documents": total_docs,
            "by_status": statuses,
            "audit_entries": audit_entries,
        }
    except Exception as e:
        return {"error": str(e)}

def launch():
    """Main entry point — start acquisition system."""
    logger.info("🚀 Launching KLAUS Autonomous Legal Knowledge Acquisition System")
    telegram("🚀 Autonomous Legal Knowledge Acquisition System starting...")

    # Ensure seed sources are in the DB
    _ensure_seeds()
    sources = list_sources()
    telegram(f"📋 {len(sources)} legal sources configured")
    for s in sources:
        logger.info(f"  Source: {s['domain']} (tier={s['tier']}, status={s['status']})")

    # Show initial state
    stats = get_store_stats()
    logger.info(f"Initial state: {stats}")
    telegram(f"📊 Initial state: {stats.get('total_documents', 0)} documents in store, "
             f"{stats.get('active_sources', 0)} active sources")

    # Start the scheduler (background jobs + workers)
    telegram("⏳ Starting scheduled jobs (daily/weekly/monthly/quarterly) + background workers...")
    start_scheduler()
    logger.info("Scheduler and background workers started")

    # Trigger an immediate daily scan to kick things off
    telegram("🔍 Triggering initial discovery scan...")
    trigger_job_now("klaus_daily")
    logger.info("Initial daily scan triggered")

    # Background workers are now running in daemon threads
    # Discovery worker runs every hour, ingestion worker every 5 min
    # Monitor and report periodically
    telegram("✅ Acquisition system running! Monitoring progress...")

    # Keep the process alive and report periodically
    start_time = time.time()
    while True:
        time.sleep(120)  # Check every 2 minutes
        elapsed = time.time() - start_time
        stats = get_store_stats()
        logger.info(f"Status at {elapsed/60:.0f}min: {stats}")

        total = stats.get("total_documents", 0)
        by_status = stats.get("by_status", {})
        approved = by_status.get("approved", 0)
        pending = by_status.get("pending", 0)
        flagged = by_status.get("flagged", 0)

        report = f"📊 KLAUS Status ({elapsed/60:.0f}min):\n"
        report += f"  • Documents: {total} total\n"
        report += f"  • Approved: {approved} | Pending: {pending} | Flagged: {flagged}\n"
        report += f"  • Audit entries: {stats.get('audit_entries', 0)}"

        # Don't spam Telegram — report every 10 minutes or on significant changes
        if elapsed % 600 < 120:  # Roughly every 10 min
            telegram(report)

        # After 30 minutes, send a summary and keep running
        if 1740 < elapsed < 1860:  # ~30 min
            telegram(f"⏰ 30-minute milestone:\n{report}\nSystem continues running in background.")

        logger.info(report)

if __name__ == "__main__":
    launch()
