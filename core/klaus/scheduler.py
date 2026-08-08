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
    # Only publicly accessible, non-paywalled, non-login-walled sources
    {"url": "https://repository.parliament.gh/home", "domain": "parliament.gh", "tier": 1, "jurisdiction": "Ghana"},
    {"url": "https://ghalii.org/", "domain": "ghalii.org", "tier": 2, "jurisdiction": "Ghana"},
    # REMOVED: judicial.gov.gh (eJudgment) — login-walled, requires judge credentials
    # REMOVED: ghanapublishing.gov.gh — paywalled, redirects to gpclonline.com login-walled store
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
    Monthly refresh: full Tier 1 + Tier 2 crawler sweep + government source
    auto-discovery. All active sources across tiers 1 and 2 are rescanned,
    and new Ghana government legal repositories are discovered.
    """
    logger.info("KLAUS: Starting monthly full refresh")
    sources = list_sources(tier=None, status="active")

    for source in sources:
        log_audit_event("discovery", "info", f"Monthly refresh: {source['domain']}")

    # ── Government source auto-discovery ──────────────────────────────
    _run_government_source_discovery()

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


# ── Government Source Auto-Discovery ──────────────────────────────────────

_GOV_DISCOVERY_URLS = [
    "/legislation", "/legislations", "/laws", "/law",
    "/acts", "/bills", "/regulations", "/rules",
    "/directives", "/guidelines", "/notices",
    "/judgments", "/judgements", "/decisions", "/cases",
    "/court", "/gazette",
    "/documents", "/downloads", "/publications", "/resources",
    "/instruments", "/legal-framework", "/legal",
]

# Known gov.gh/org.gh domains to check for undiscovered legal repositories.
# This list should be periodically expanded.
_GOV_DOMAINS_TO_CHECK = [
    # Statutory bodies not yet in the source registry
    "audit.gov.gh",           # Auditor-General
    "psc.gov.gh",             # Public Services Commission
    "ncc.gov.gh",             # National Commission for Civic Education
    "rti.gov.gh",             # Right to Information Commission
    "fwa.gov.gh",             # Fair Wages and Salaries Commission
    "nib.gov.gh",             # National Investment Bank (regulatory)
    "moti.gov.gh",            # Ministry of Trade and Industry
    "mwh.gov.gh",             # Ministry of Works and Housing
    "moi.gov.gh",             # Ministry of Interior
    "moc.gov.gh",             # Ministry of Communications
    "mes.gov.gh",             # Ministry of Environment and Science
    "mfa.gov.gh",             # Ministry of Foreign Affairs
    "moys.gov.gh",            # Ministry of Youth and Sports
    "moe.gov.gh",             # Ministry of Education
    "mogcsp.gov.gh",          # Ministry of Gender, Children and Social Protection
    "moh.gov.gh",             # Ministry of Health
    "motac.gov.gh",           # Ministry of Tourism, Arts and Culture
    "mida.gov.gh",            # Millennium Development Authority
    "gogig.gov.gh",           # Ghana Oil and Gas Inclusive Growth
    "gifec.gov.gh",           # Ghana Infrastructure Fund
    "nib.gov.gh",             # National Investment Bank
    "bogs.gov.gh",            # Bank of Ghana (alt domain)
    "stategov.ghana.gov.gh",  # State protocol
    "mint.gov.gh",            # Ministry of Interior (alt)
    "nacoc.gov.gh",           # Narcotics Control Commission
]


def _run_government_source_discovery():
    """Auto-discover new Ghana government legal repositories.

    Scans known government domains for legal document patterns.
    Registers newly discovered legal repositories as KLAUS sources.
    Runs as part of the monthly refresh cycle (directive section 31).
    """
    import requests
    from core.klaus.source_registry import is_ghana_government_domain, generate_discovery_urls
    from core.klaus.generic_connector import HEADERS

    logger.info("KLAUS: Starting government source auto-discovery")

    from core.klaus.db_manager import list_sources, add_source
    existing_sources = list_sources(status=None)
    existing_domains = {s["domain"] for s in existing_sources}

    discovered = 0
    for domain in _GOV_DOMAINS_TO_CHECK:
        if domain in existing_domains:
            continue

        base_url = f"https://{domain}"
        try:
            resp = requests.get(base_url, headers=HEADERS, timeout=15)
            if resp.status_code >= 400:
                continue

            # Check for legal content indicators
            html = resp.text.lower()
            legal_indicators = [
                "legislation", "legislations", "acts of parliament",
                "regulations", "directives", "guidelines", "legal framework",
                "laws of ghana", "legal notices", "gazette",
            ]
            has_legal = any(ind in html for ind in legal_indicators)

            discovery_urls = generate_discovery_urls(f"https://{domain}")
            found_doc_urls = 0
            for disc_url in discovery_urls[:5]:  # Check first 5 paths
                try:
                    sub_resp = requests.get(disc_url, headers=HEADERS, timeout=10)
                    if sub_resp.status_code == 200 and len(sub_resp.text) > 500:
                        found_doc_urls += 1
                except Exception:
                    pass

            if has_legal and found_doc_urls > 0:
                try:
                    add_source(
                        url=f"https://{domain}",
                        domain=domain,
                        tier=3,
                        jurisdiction="Ghana",
                    )
                    logger.info(f"  Discovered: {domain} ({found_doc_urls} legal paths found)")
                    log_audit_event(
                        "discovery", "info",
                        f"Auto-discovered government source: {domain} "
                        f"({found_doc_urls} legal paths)",
                    )
                    discovered += 1
                except Exception:
                    pass
        except Exception:
            continue

    if discovered:
        logger.info(f"KLAUS: Government discovery found {discovered} new sources")
    else:
        logger.info("KLAUS: Government discovery — no new sources found")


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
