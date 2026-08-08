#!/usr/bin/env python3
"""
KLAUS Phase 5: Historical Backfill Runner

For each UNVERIFIED source:
  1. Test reachability (HTTP HEAD → homepage)
  2. Check for paywall/login-wall indicators
  3. Run multi-strategy discovery if reachable + open
  4. Process documents through the existing pipeline
  5. Record results to audit log

Runs tier-by-tier: Tier 1 → Tier 2 → Tier 3
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone

import requests

# project root
sys.path.insert(0, "/project/ai-orchestrator")

from core.klaus.source_registry import (
    get_unverified_sources,
    get_permitted_sources,
    GhanaLegalSource,
)
from core.klaus.generic_connector import multi_strategy_discover, HEADERS, REQUEST_TIMEOUT
from core.klaus.db_manager import (
    add_source,
    list_sources,
    log_audit_event,
    get_cursor,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("klaus.backfill")

# Only flag ACTUAL paywall patterns, not incidental keywords like "login" or "register"
# which appear on nearly every government site's admin/staff links.
PAYWALL_INDICATORS = [
    "subscribe to access", "subscribe to view", "subscription required",
    "paid subscription", "premium content", "premium access",
    "paywall", "purchase this document", "purchase document",
    "buy this document", "buy now", "add to cart",
    "payment required", "checkout", "billing",
    "unlock this", "unlock full", "get full access",
    "paid plan", "pricing plan", "choose your plan",
]

LOGIN_WALL_INDICATORS = [
    "please log in to access", "please login to access",
    "you must be logged in", "you must be signed in",
    "authentication required to view", "sign in to continue",
    "log in to view", "login to view",
    "access denied", "403 forbidden",
    "restricted access", "authorized personnel only",
    "member login required", "login required",
]


def test_reachability(source: GhanaLegalSource) -> dict:
    """Test if a source is reachable and check for access barriers."""
    result = {
        "source_key": source.key,
        "domain": source.domain,
        "base_url": source.base_url,
        "tier": source.tier,
        "reachable": False,
        "status_code": None,
        "paywall_detected": False,
        "login_wall_detected": False,
        "error": None,
    }

    try:
        resp = requests.get(source.base_url, headers=HEADERS, timeout=15, allow_redirects=True)
        result["status_code"] = resp.status_code
        result["final_url"] = resp.url

        if resp.status_code == 200:
            result["reachable"] = True
            html_lower = resp.text.lower()

            # Check for paywall/login-wall indicators
            paywall_hits = [ind for ind in PAYWALL_INDICATORS if ind in html_lower]
            login_hits = [ind for ind in LOGIN_WALL_INDICATORS if ind in html_lower]

            if paywall_hits:
                result["paywall_detected"] = True
                result["paywall_indicators"] = paywall_hits[:5]
            if login_hits:
                result["login_wall_detected"] = True
                result["login_indicators"] = login_hits[:5]

            # Count potential legal documents
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            result["total_links"] = len(soup.find_all("a", href=True))
            result["pdf_links"] = len(soup.find_all("a", href=lambda h: h and h.lower().endswith(".pdf")))

            # Title
            result["page_title"] = soup.title.text.strip()[:200] if soup.title else ""

        elif resp.status_code in (401, 403):
            result["login_wall_detected"] = True
            result["error"] = f"HTTP {resp.status_code}"
        else:
            result["error"] = f"HTTP {resp.status_code}"

    except requests.Timeout:
        result["error"] = "timeout"
    except requests.ConnectionError as e:
        result["error"] = f"connection_error: {e}"
    except Exception as e:
        result["error"] = str(e)[:200]

    return result


# Sources known to be hijacked or serving unrelated content
HIJACKED_DOMAINS = {
    # wrc-gh.org was hijacked → corrected to wrc.gov.gh 2026-08-07
}


def run_discovery(source: GhanaLegalSource, reachability: dict) -> dict:
    """Run multi-strategy discovery on a reachable source."""
    result = {
        "source_key": source.key,
        "documents_found": 0,
        "discovery_methods": [],
        "errors": [],
        "blocked": False,
        "block_reason": "",
    }

    # Skip hijacked domains
    if source.domain in HIJACKED_DOMAINS:
        result["blocked"] = True
        result["block_reason"] = f"domain_hijacked: {HIJACKED_DOMAINS[source.domain]}"
        return result

    # Note barriers but still attempt discovery — the connector itself is smart enough
    # to only grab publicly accessible PDFs. Government sites often have "subscribe to
    # newsletter" links that trigger false positive paywall detection.
    if reachability.get("paywall_detected"):
        result["note"] = f"paywall_indicators: {reachability.get('paywall_indicators','')}"
    if reachability.get("login_wall_detected"):
        result["note"] = (result.get("note", "") +
                          f" login_indicators: {reachability.get('login_indicators','')}")

    try:
        docs = multi_strategy_discover(
            source_url=source.base_url,
            source_domain=source.domain,
            discovery_urls=source.discovery_urls if source.discovery_urls else None,
            acquisition_status=source.acquisition_status,
        )

        result["documents_found"] = len(docs)

        # Track which methods yielded results
        methods = set()
        for doc in docs:
            methods.add(doc.get("discovery_method", "unknown"))
        result["discovery_methods"] = sorted(methods)

        # Check if any are blocked by rights gate
        blocked = [d for d in docs if d.get("_acquisition_blocked")]
        if blocked:
            result["blocked"] = True
            result["block_reason"] = blocked[0].get("_block_reason", "rights_gate")

        # Process through existing pipeline
        if docs and not result["blocked"]:
            try:
                from core.klaus.background_workers import process_discovered_documents
                process_discovered_documents(docs, source.domain)
                result["processed"] = True
            except Exception as e:
                result["errors"].append(f"process: {e}")
                result["processed"] = False

    except Exception as e:
        result["errors"].append(f"discovery: {e}")

    return result


def run_backfill(tiers: list = None, resume_from: str = None):
    """Main backfill runner.  Defaults to all tiers."""
    sources = get_unverified_sources()

    # Filter by tier if specified
    if tiers:
        sources = [s for s in sources if s.tier in tiers]

    # Resume support
    if resume_from:
        skip = True
        filtered = []
        for s in sources:
            if skip and s.key == resume_from:
                skip = False
            if not skip:
                filtered.append(s)
        sources = filtered
        logger.info(f"Resuming from {resume_from} — {len(sources)} sources remaining")

    logger.info(f"=== KLAUS Historical Backfill: {len(sources)} UNVERIFIED sources ===")
    logger.info(f"Tiers: {tiers or 'all'}")

    results = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "total_sources": len(sources),
        "reachability": [],
        "discovery": [],
        "summary": {
            "reachable": 0,
            "unreachable": 0,
            "paywalled": 0,
            "login_walled": 0,
            "documents_total": 0,
            "sources_with_docs": 0,
        },
    }

    for i, source in enumerate(sources):
        logger.info(f"\n[{i+1}/{len(sources)}] {source.key} ({source.domain}) tier={source.tier}")
        log_audit_event("backfill", "info", f"Phase5: starting {source.key} ({source.domain})")

        # Step 1: Reachability
        reach = test_reachability(source)
        results["reachability"].append(reach)

        if reach["reachable"]:
            results["summary"]["reachable"] += 1
            logger.info(f"  ✅ reachable (200) — {reach.get('page_title', '')[:80]}")
            if reach.get("pdf_links", 0) > 0:
                logger.info(f"  📄 {reach['pdf_links']} PDF links on homepage")

            if reach.get("paywall_detected"):
                results["summary"]["paywalled"] += 1
                logger.warning(f"  💰 PAYWALL: {reach.get('paywall_indicators', [])}")
            if reach.get("login_wall_detected"):
                results["summary"]["login_walled"] += 1
                logger.warning(f"  🔒 LOGIN-WALL: {reach.get('login_indicators', [])}")

            # Step 2: Discovery
            if not reach.get("paywall_detected") and not reach.get("login_wall_detected"):
                disc = run_discovery(source, reach)
            else:
                disc = {
                    "source_key": source.key,
                    "documents_found": 0,
                    "discovery_methods": [],
                    "errors": [],
                    "blocked": True,
                    "block_reason": "paywall_or_login",
                }

            results["discovery"].append(disc)
            results["summary"]["documents_total"] += disc.get("documents_found", 0)
            if disc.get("documents_found", 0) > 0:
                results["summary"]["sources_with_docs"] += 1
                logger.info(f"  📚 {disc['documents_found']} documents found via {disc.get('discovery_methods', [])}")

            if disc.get("blocked"):
                log_audit_event(
                    "backfill", "warning",
                    f"Phase5: {source.key} blocked — {disc['block_reason']}",
                )
        else:
            results["summary"]["unreachable"] += 1
            logger.warning(f"  ❌ unreachable: {reach.get('error', 'unknown')}")
            log_audit_event("backfill", "warning",
                f"Phase5: {source.key} unreachable — {reach.get('error', 'unknown')}")

        # Rate limiting
        time.sleep(1)

    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    results["duration_seconds"] = (
        datetime.fromisoformat(results["completed_at"]) -
        datetime.fromisoformat(results["started_at"])
    ).total_seconds()

    # Save results
    outpath = "/project/ai-orchestrator/memory/klaus_backfill_results.json"
    with open(outpath, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"\n=== Results saved to {outpath} ===")

    return results


def print_summary(results: dict):
    s = results["summary"]
    print("\n" + "=" * 60)
    print("KLAUS PHASE 5 BACKFILL — SUMMARY")
    print("=" * 60)
    print(f"Sources tested:   {results['total_sources']}")
    print(f"✅ Reachable:      {s['reachable']}")
    print(f"❌ Unreachable:    {s['unreachable']}")
    print(f"💰 Paywalled:      {s['paywalled']}")
    print(f"🔒 Login-walled:   {s['login_walled']}")
    print(f"📚 Docs found:     {s['documents_total']}")
    print(f"📂 Sources w/docs: {s['sources_with_docs']}")
    print(f"⏱ Duration:       {results['duration_seconds']:.1f}s")
    print("=" * 60)

    # Per-source detail
    print("\n--- Reachable & Open (candidates for upgrade to PERMITTED) ---")
    for r in results["reachability"]:
        if r["reachable"] and not r.get("paywall_detected") and not r.get("login_wall_detected"):
            disc = next((d for d in results["discovery"] if d["source_key"] == r["source_key"]), {})
            docs = disc.get("documents_found", 0)
            methods = disc.get("discovery_methods", [])
            print(f"  {r['source_key']:30s} | tier {r['tier']} | {r['domain']:35s} | {docs} docs | {methods}")

    print("\n--- Paywalled / Login-walled (keep as RESTRICTED) ---")
    for r in results["reachability"]:
        if r["reachable"] and (r.get("paywall_detected") or r.get("login_wall_detected")):
            flags = []
            if r.get("paywall_detected"): flags.append("PAYWALL")
            if r.get("login_wall_detected"): flags.append("LOGIN")
            print(f"  {r['source_key']:30s} | tier {r['tier']} | {r['domain']:35s} | {','.join(flags)}")

    print("\n--- Unreachable ---")
    for r in results["reachability"]:
        if not r["reachable"]:
            print(f"  {r['source_key']:30s} | tier {r['tier']} | {r['domain']:35s} | {r.get('error','?')}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="KLAUS Historical Backfill Runner")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], nargs="*",
                        help="Limit to specific tiers (default: all)")
    parser.add_argument("--resume-from", type=str,
                        help="Resume from a specific source key")
    parser.add_argument("--dry-run", action="store_true",
                        help="Test reachability only, skip discovery")
    args = parser.parse_args()

    tiers = args.tier if args.tier else None

    if args.dry_run:
        sources = get_unverified_sources()
        if tiers:
            sources = [s for s in sources if s.tier in tiers]
        logger.info(f"DRY RUN: testing reachability of {len(sources)} sources")
        for source in sources:
            reach = test_reachability(source)
            status = "✅" if reach["reachable"] else "❌"
            flags = ""
            if reach.get("paywall_detected"): flags += " 💰"
            if reach.get("login_wall_detected"): flags += " 🔒"
            logger.info(f"  {status} {source.key:30s} | {source.domain:35s} | {reach.get('page_title', '')[:60]}{flags} | PDFs={reach.get('pdf_links', 0)}")
        sys.exit(0)

    results = run_backfill(tiers=tiers, resume_from=args.resume_from)
    print_summary(results)
