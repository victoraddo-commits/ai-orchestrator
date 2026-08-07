#!/usr/bin/env python3
"""
Phase 18C Migration: 16-Tier Classification + Legal Authority Records

1. Seeds 16 acquisition tiers in klaus_acquisition_tiers
2. Classifies all existing documents into correct tiers
3. Creates initial Legal Authority Records from existing metadata
4. Updates tier acquisition counts

Run: .venv/bin/python -m core.klaus.migrate_to_16tiers
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.klaus.db_manager import (
    get_cursor,
    get_document,
    get_source,
    seed_acquisition_tiers,
    set_document_tier,
    insert_authority_record,
    get_authority_record,
    update_authority_record,
    update_tier_acquisition_count,
    get_tier_coverage_stats,
    count_documents_by_tier,
    log_audit_event,
)
from core.klaus.schema import ACQUISITION_TIERS, get_tier_priority_band
from core.klaus.quality_agents import TierClassificationAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("klaus.migrate_to_16tiers")


def seed_tiers():
    """Seed the 16 acquisition tiers."""
    count = seed_acquisition_tiers()
    logger.info(f"✅ Seeded {count} acquisition tiers")
    for tn in range(1, 17):
        info = ACQUISITION_TIERS[tn]
        logger.info(f"   T{tn:2d} {info['name']:35s} band={get_tier_priority_band(tn)} target={info['target']}")


def classify_all_documents():
    """Run TierClassificationAgent on all existing documents."""
    with get_cursor() as cur:
        cur.execute("SELECT id, title, category FROM klaus_documents ORDER BY id")
        docs = cur.fetchall()

    logger.info(f"Classifying {len(docs)} documents into 16-tier system...")
    classified = 0
    tier_counts = {}

    for doc in docs:
        try:
            tier_num, authority_type, confidence = TierClassificationAgent.classify(doc["id"])
            set_document_tier(doc["id"], tier_num)

            tier_counts[tier_num] = tier_counts.get(tier_num, 0) + 1
            logger.info(
                f"  #{doc['id']:3d} → T{tier_num:2d} ({ACQUISITION_TIERS[tier_num]['name']:35s}) "
                f"type={authority_type:12s} conf={confidence:6s} | {doc['title'][:50]}"
            )
            classified += 1
        except Exception as e:
            logger.warning(f"  #{doc['id']:3d} classification failed: {e}")

    logger.info(f"✅ Classified {classified}/{len(docs)} documents")
    logger.info("Tier distribution:")
    for tn in sorted(tier_counts):
        logger.info(f"  T{tn:2d}: {tier_counts[tn]:3d} documents")
    return tier_counts


def create_authority_records():
    """Create initial Legal Authority Records for all documents."""
    with get_cursor() as cur:
        cur.execute("SELECT id FROM klaus_documents ORDER BY id")
        docs = cur.fetchall()

    logger.info(f"Creating Legal Authority Records for {len(docs)} documents...")
    created = 0
    skipped = 0

    for doc_row in docs:
        doc_id = doc_row["id"]
        doc = get_document(doc_id)
        if not doc:
            continue

        # Skip if already has an authority record
        existing = get_authority_record(doc_id)
        if existing:
            skipped += 1
            continue

        # Determine authority_type and tier from classification
        tier_num, authority_type, confidence = TierClassificationAgent.classify(doc_id)

        # Build initial authority record from existing metadata
        kwargs = {
            "document_id": doc_id,
            "authority_type": authority_type,
            "citation_text": _build_citation(doc),
            "court_identifier": doc.get("court") or _extract_court_from_title(doc.get("title", "")),
            "status": "current",
            "language": "en",
            "source_trust_level": "unverified",
        }

        # Add date fields if available
        if doc.get("year"):
            kwargs["date_decided"] = f"{doc['year']}-01-01"
        if doc.get("effective_date"):
            kwargs["date_argued"] = str(doc["effective_date"])

        # Add legislation number if present in title
        leg_num = _extract_legislation_number(doc.get("title", ""))
        if leg_num:
            if authority_type == "bill":
                kwargs["gazette_number"] = leg_num
            elif authority_type == "regulation":
                pass  # LI/CI numbers go in citation
            elif authority_type == "statute":
                pass  # Act numbers go in citation

        try:
            record_id = insert_authority_record(**kwargs)
            created += 1
        except Exception as e:
            logger.warning(f"  #{doc_id:3d} authority record creation failed: {e}")

    logger.info(f"✅ Created {created} new Legal Authority Records, {skipped} already exist")


def _build_citation(doc: dict) -> str:
    """Build an AGLC4-style citation from document metadata."""
    title = doc.get("title", "")
    year = doc.get("year")
    leg_num = _extract_legislation_number(title)

    # Clean the title
    clean_title = (
        title.replace(".pdf", "")
        .replace("_", " ")
        .replace("%20", " ")
        .replace("%2C", ",")
        .replace("%28", "(")
        .replace("%29", ")")
        .strip()
    )

    if leg_num:
        return f"{clean_title}"

    if year:
        return f"{clean_title} ({year})"

    return clean_title


def _extract_court_from_title(title: str) -> str:
    """Extract court name heuristically from document title."""
    title_lower = title.lower()
    courts = [
        "supreme court", "high court", "court of appeal",
        "circuit court", "district court", "fast track high court",
    ]
    for court in courts:
        if court in title_lower:
            return court.title()
    return ""


def _extract_legislation_number(title: str) -> str:
    """Extract Act/LI/CI number from title."""
    import re
    # Act XXX
    m = re.search(r"Act\s+(\d{3,4})", title, re.IGNORECASE)
    if m:
        return f"Act {m.group(1)}"
    # LI XXXX
    m = re.search(r"L\.?\s*I\.?\s+(\d+)", title, re.IGNORECASE)
    if m:
        return f"LI {m.group(1)}"
    return ""


def recalculate_counts():
    """Update all tier acquisition counts."""
    for tn in range(1, 17):
        update_tier_acquisition_count(tn)


def run():
    """Run the full migration."""
    logger.info("=" * 60)
    logger.info("Phase 18C: 16-Tier Migration")
    logger.info("=" * 60)

    # Step 1: Seed tiers
    logger.info("\n📋 Step 1: Seeding 16 acquisition tiers...")
    seed_tiers()

    # Step 2: Classify documents
    logger.info("\n📋 Step 2: Classifying documents into 16-tier system...")
    tier_counts = classify_all_documents()

    # Step 3: Create authority records
    logger.info("\n📋 Step 3: Creating Legal Authority Records...")
    create_authority_records()

    # Step 4: Recalculate counts
    logger.info("\n📋 Step 4: Recalculating tier acquisition counts...")
    recalculate_counts()

    # Step 5: Show coverage stats
    logger.info("\n📋 Step 5: Coverage statistics...")
    stats = get_tier_coverage_stats()
    print("\n" + "=" * 80)
    print(f"{'Tier':5s} {'Name':35s} {'Count':6s} {'Target':7s} {'Coverage':>9s} {'Band':14s}")
    print("-" * 80)
    for s in stats:
        tn = s["tier_number"]
        coverage = f"{s['coverage_pct']:.1f}%"
        status_icon = "✅" if s["actual_count"] > 0 else "⬜"
        print(
            f"{status_icon} T{tn:<3d} {s['tier_name']:35s} "
            f"{s['actual_count']:>4d}   {s['coverage_target']:>4d}   "
            f"{coverage:>8s}  {get_tier_priority_band(tn):14s}"
        )
    print("=" * 80)

    logger.info("\n✅ Migration complete!")


if __name__ == "__main__":
    run()
