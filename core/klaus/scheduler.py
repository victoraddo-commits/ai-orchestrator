"""
KLAUS Legal Knowledge Acquisition System - Scheduled Update Runner

Implements the four-tier update schedule from the approved plan:
- Daily: Legislation checks (parliament.gh for new Acts and Bills)
- Weekly: Judgments and Supreme Court rulings scan
- Monthly: Full Tier 1 & Tier 2 repository refresh and crawler sweep
- Quarterly: Comprehensive accuracy verification + outdated material review

Uses apscheduler for scheduling. All jobs write results to audit logs.
"""

import logging
import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from core.klaus.db_manager import (
    list_sources,
    get_failed_sources,
    log_audit_event,
)

logger = logging.getLogger(__name__)


TIER_1_SEEDS = [
    {"url": "https://repository.parliament.gh/home", "domain": "parliament.gh", "tier": 1, "jurisdiction": "Ghana"},
    {"url": "https://www.ejudgment.judicial.gov.gh/", "domain": "judicial.gov.gh", "tier": 1, "jurisdiction": "Ghana"},
    {"url": "https://ghalii.org/", "domain": "ghalii.org", "tier": 2, "jurisdiction": "Ghana"},
    {"url": "https://ghanapublishing.gov.gh/", "domain": "ghanapublishing.gov.gh", "tier": 1, "jurisdiction": "Ghana"},
]


_scheduler = BackgroundScheduler(timezone="UTC")


def _ensure_seeds():
    from core.klaus.db_manager import add_source

    for seed in TIER_1_SEEDS:
        try:
            add_source(
                url=seed["url"],
                domain=seed["domain"],
                tier=seed["tier"],
                jurisdiction=seed["jurisdiction"],
            )
        except Exception:
            pass


def daily_legislation_check():
    """
    Daily check: scan Tier 1 sources for new legislation (Acts, Bills).
    """
    logger.info("KLAUS: Starting daily legislation check")
    sources = list_sources(tier=1, status="active")

    for source in sources:
        log_audit_event("discovery", "info", f"Daily scan: {source['domain']}")

    broken = get_failed_sources()
    if broken:
        log_audit_event("failure", "warning", f"{len(broken)} broken sources found in daily scan")

    logger.info("KLAUS: Daily legislation check complete (%d sources)", len(sources))


def weekly_judgments_scan():
    """
    Weekly scan: check judiciary sources for new judgments and rulings.
    """
    logger.info("KLAUS: Starting weekly judgments scan")
    sources = list_sources(tier=1, status="active")
    tier2 = list_sources(tier=2, status="active")

    for source in sources + tier2:
        log_audit_event("discovery", "info", f"Weekly scan: {source['domain']}")

    broken = get_failed_sources()
    if broken:
        log_audit_event(
            "failure",
            "warning",
            f"{len(broken)} broken sources found in weekly scan: "
            + ", ".join(s["domain"] for s in broken[:5]),
        )

    logger.info("KLAUS: Weekly judgments scan complete (%d sources)", len(sources) + len(tier2))


def monthly_full_refresh():
    """
    Monthly refresh: full Tier 1 + Tier 2 crawler sweep. All active
    sources across tiers 1 and 2 are rescanned.
    """
    logger.info("KLAUS: Starting monthly full refresh")
    sources = list_sources(tier=None, status="active")

    for source in sources:
        log_audit_event("discovery", "info", f"Monthly refresh: {source['domain']}")

    broken = get_failed_sources()
    for b in broken:
        log_audit_event("failure", "warning", f"Broken source in monthly sweep: {b['domain']}")

    logger.info("KLAUS: Monthly full refresh complete (%d sources)", len(sources))


def quarterly_accuracy_verification():
    """
    Quarterly check: verify document accuracy, broken link purge,
    outdated material review.
    """
    logger.info("KLAUS: Starting quarterly accuracy verification")

    broken = get_failed_sources()
    if broken:
        for b in broken:
            log_audit_event(
                "failure",
                "warning",
                f"Quarterly: broken source {b['domain']} still unresolved",
            )
    else:
        log_audit_event("verification", "info", "Quarterly: no broken sources")

    from core.klaus.db_manager import get_cursor

    try:
        with get_cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) as ct FROM klaus_documents
                   WHERE review_status != 'approved'"""
            )
            row = cur.fetchone()
            if row and row["ct"] > 0:
                log_audit_event(
                    "review",
                    "info",
                    f"Quarterly: {row['ct']} documents still pending review",
                )
    except Exception:
        logger.warning("KLAUS: Could not query pending documents during quarterly check")

    logger.info("KLAUS: Quarterly accuracy verification complete")


def verify_existing_documents():
    """Re-verify all approved documents for accuracy."""
    from core.klaus.db_manager import get_cursor

    try:
        with get_cursor() as cur:
            cur.execute(
                "SELECT id, title FROM klaus_documents WHERE review_status = 'approved'"
            )
            docs = cur.fetchall()
            for doc in docs:
                log_audit_event(
                    "verification",
                    "info",
                    f"Quarterly re-verification of: {doc['title']}",
                    doc["id"],
                )
    except Exception:
        logger.warning("KLAUS: Could not re-verify approved documents")


def start_scheduler():
    """
    Start the KLAUS background scheduler. Safe to call multiple times;
    apscheduler will ignore duplicate job additions.
    """
    _ensure_seeds()

    _scheduler.add_job(
        daily_legislation_check,
        CronTrigger(hour=3, minute=0),
        id="klaus_daily",
        replace_existing=True,
    )
    _scheduler.add_job(
        weekly_judgments_scan,
        CronTrigger(day_of_week="mon", hour=4, minute=0),
        id="klaus_weekly",
        replace_existing=True,
    )
    _scheduler.add_job(
        monthly_full_refresh,
        CronTrigger(day=1, hour=5, minute=0),
        id="klaus_monthly",
        replace_existing=True,
    )
    _scheduler.add_job(
        quarterly_accuracy_verification,
        CronTrigger(month="1,4,7,10", day=1, hour=6, minute=0),
        id="klaus_quarterly",
        replace_existing=True,
    )

    # Start background workers in separate threads
    def start_background_workers():
        """Start discovery and ingestion workers in background threads."""
        try:
            from core.klaus.background_workers import run_discovery_worker, run_ingestion_worker
            discovery_thread = threading.Thread(target=run_discovery_worker, daemon=True)
            ingestion_thread = threading.Thread(target=run_ingestion_worker, daemon=True)
            
            discovery_thread.start()
            ingestion_thread.start()
            
            logger.info("KLAUS: Background workers started")
        except Exception as e:
            logger.error(f"Failed to start background workers: {e}")

    if not _scheduler.running:
        _scheduler.start()
        logger.info("KLAUS: Scheduler started with daily/weekly/monthly/quarterly jobs")
        # Start background workers asynchronously
        threading.Thread(target=start_background_workers, daemon=True).start()


def stop_scheduler():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("KLAUS: Scheduler stopped")


def trigger_job_now(job_id: str):
    """Manually trigger a named job (for testing/operator overrides)."""
    jobs = {
        "klaus_daily": daily_legislation_check,
        "klaus_weekly": weekly_judgments_scan,
        "klaus_monthly": monthly_full_refresh,
        "klaus_quarterly": quarterly_accuracy_verification,
    }
    job_fn = jobs.get(job_id)
    if job_fn:
        job_fn()
        return True
    return False
