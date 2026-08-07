"""Apply Kai Legal Brain roadmap refactoring based on 2026-08-06 directive.

Actions:
  1. REMOVE 8 phases (duplicates, superseded, or already-complete)
  2. RESURRECT 4 failed phases with corrected scope
  3. CREATE 3 new phases
  4. CLEAN up 17P duplicate
"""

import json
import sys
from datetime import datetime, timezone

ROADMAP_PATH = "roadmap.json"

# Phase IDs to DELETE
PHASES_TO_DELETE = [
    "TK-96ddd758",   # 17O-B duplicate proposal
    "TK-44eee510",   # 17O-C duplicate proposal
    "TK-525d42a6",   # 17O-E absorbed into 18C-NEW
    "TK-d569b912",   # 17O-H already implemented in klaus/scheduler.py
    "13I",           # Future Roadmap Generator (failed, superseded)
]

# Phase titles to DELETE (by title match — some have non-standard IDs)
PHASE_TITLES_TO_DELETE = [
    "17P [duplicate — remove]",  # We'll handle 17P specially
]

# Phases to SKIP deleting by title prefix match (keep completed 17P)
# The actual 17P is titled "Juris Kai Multi-Tenant" — we keep that one
# If there's a second one, we remove it

def main():
    with open(ROADMAP_PATH) as f:
        roadmap = json.load(f)

    phases = roadmap.get("phases", [])
    original_count = len(phases)
    print(f"Original phase count: {original_count}")

    # Count current state distribution
    states = {}
    for p in phases:
        s = p.get("status", "unknown")
        states[s] = states.get(s, 0) + 1
    print(f"Current state distribution: {states}")

    # Step 1: Delete by ID
    new_phases = []
    removed_ids = []
    for p in phases:
        pid = p.get("id", "")
        if pid in PHASES_TO_DELETE:
            removed_ids.append(pid)
            print(f"  REMOVED: {pid} ({p.get('title', 'unknown')})")
            continue
        new_phases.append(p)

    print(f"Removed {len(removed_ids)} phases by ID")

    # Step 2: Handle 17P — keep only the completed one
    phase_17p_entries = [p for p in new_phases if "17P" in p.get("id", "") or "17P" in p.get("title", "")]
    if len(phase_17p_entries) > 1:
        # Keep the completed one
        kept = None
        for p in phase_17p_entries:
            if p.get("status") == "completed":
                if kept is None:
                    kept = p
                    print(f"  KEPT: {p.get('id')} (completed)")
                else:
                    new_phases = [x for x in new_phases if x is not p]
                    print(f"  REMOVED DUPLICATE: {p.get('id')} ({p.get('status')})")
            else:
                new_phases = [x for x in new_phases if x is not p]
                print(f"  REMOVED DUPLICATE: {p.get('id')} ({p.get('status')})")
    elif len(phase_17p_entries) == 0:
        print("Warning: No 17P entries found")

    # Step 3: Remove 17G (UI Polish) and 17N if present
    for p in new_phases[:]:
        title = p.get("title", "")
        if title.lower() == "17g" or "ui polish" in title.lower():
            new_phases.remove(p)
            print(f"  REMOVED: {p.get('id')} ({title})")
        # 17N — if it exists and is not the Voice/Phone completed
        if title.lower() == "17n" and p.get("status") != "completed":
            new_phases.remove(p)
            print(f"  REMOVED: {p.get('id')} ({title})")

    # Step 4: Resurrect 18C with corrected scope
    for p in new_phases:
        if "18C" in p.get("id", "") or ("18C" in p.get("title", "") and "zero" in p.get("title", "").lower()):
            old_status = p["status"]
            p["status"] = "pending"
            p["title"] = "18C: Zero-Trust Legal Brain Architecture"
            p["description"] = (
                "Complete zero-trust Legal Brain: dedicated PostgreSQL DB, immutable WORM storage, "
                "hash-chain document verification, sandboxed PDF processing (subprocess+seccomp), "
                "ClamAV malware scanning, temp workspace isolation (per-session SQLite on tmpfs), "
                "permanent/temporary service boundary, 6-stage ingestion pipeline, "
                "migration of existing approved klaus_documents to new architecture."
            )
            p.setdefault("dependencies", [])
            p["dependencies"] = ["17O-D", "18A-a", "18A-b"]
            print(f"  RESURRECTED: {p['id']} ({old_status} → pending) — 18C-NEW scope")
            break

    # Step 5: Resurrect 19E with Legal-specific scope
    for p in new_phases:
        if "19E" in p.get("id", "") or ("19E" in p.get("title", "") and "knowledge" in p.get("title", "").lower()):
            old_status = p["status"]
            p["status"] = "pending"
            p["title"] = "19E: Legal Knowledge Engine"
            p["description"] = (
                "Legal-specific knowledge graph: extract entities (courts, judges, statutes, principles) from "
                "permanent documents, structured citation network (Act→Constitution→Case), Ghana-only Phase 1 "
                "with plugin architecture for future jurisdictions, version awareness (amendments, repeals, "
                "judicial treatment: overruled/distinguished/followed), source trust scoring, query API."
            )
            p.setdefault("dependencies", [])
            p["dependencies"] = ["18C"]
            print(f"  RESURRECTED: {p['id']} ({old_status} → pending) — 19E-NEW scope")
            break

    # Step 6: Resurrect 19L with Legal-specific scope
    for p in new_phases:
        if "19L" in p.get("id", "") or ("19L" in p.get("title", "") and "trust" in p.get("title", "").lower()):
            old_status = p["status"]
            p["status"] = "pending"
            p["title"] = "19L: Legal Trust Engine"
            p["description"] = (
                "Legal-specific trust scoring: per-source reliability scores (Tier 1 official > Tier 3 secondary), "
                "per-document classification confidence from QC agents, citation verification against knowledge graph, "
                "AI response confidence scoring based on source quality + citation coverage, "
                "auto-flag responses with confidence < 0.7 for operator review."
            )
            p.setdefault("dependencies", [])
            p["dependencies"] = ["19E"]
            print(f"  RESURRECTED: {p['id']} ({old_status} → pending) — 19L-NEW scope")
            break

    # Step 7: Resurrect 19Q with Legal-specific scope
    for p in new_phases:
        if "19Q" in p.get("id", "") or ("19Q" in p.get("title", "") and ("health" in p.get("title", "").lower() or "integrity" in p.get("title", "").lower())):
            old_status = p["status"]
            p["status"] = "pending"
            p["title"] = "19Q: Legal Brain Health & Integrity"
            p["description"] = (
                "Continuous integrity monitoring for Legal Brain: daily hash verification of all permanent documents "
                "(SHA-256), missing publication detection vs official source sitemaps, citation integrity checks, "
                "hash-based dedup across entire corpus, conflicting version detection, embedding integrity validation, "
                "source availability monitoring, Telegram alerts for any integrity failures."
            )
            p.setdefault("dependencies", [])
            p["dependencies"] = ["18C", "19E"]
            print(f"  RESURRECTED: {p['id']} ({old_status} → pending) — 19Q-NEW scope")
            break

    # Step 8: Create 3 new phases
    now = datetime.now(timezone.utc).isoformat()

    new_phase_18D = {
        "id": "18D",
        "title": "18D: Research Session Logging",
        "description": (
            "Every legal query generates a reproducible Research Session: session_id, user_id, query, "
            "retrieved_authorities, search_strategy, citations used, model, confidence, brain_version, "
            "timestamp. Append-only log in Legal Brain's dedicated DB. API: GET /legal-brain/v1/sessions. "
            "Export to JSON/PDF. Privacy: user-uploaded docs NOT stored, only permanent corpus doc IDs."
        ),
        "status": "pending",
        "dependencies": ["18C"],
        "priority": 5,
        "module": "legal_brain",
        "created_at": now,
        "updated_at": now,
    }
    new_phases.append(new_phase_18D)
    print(f"  CREATED: 18D — Research Session Logging")

    new_phase_18E = {
        "id": "18E",
        "title": "18E: Legal Brain Command Center",
        "description": (
            "Merge all Legal Brain management into Kai Command Center: Source Registry, Download Queue, "
            "Verification Pipeline, Malware Scanning, OCR Processing, Metadata Extraction, Citation Index, "
            "Version History, Integrity Monitoring, Audit Ledger, Research Sessions, Temporary Workspaces, "
            "Storage Analytics, Performance Metrics, Scheduler, Agent Health — 16+ panels."
        ),
        "status": "pending",
        "dependencies": ["18C", "18D", "13O"],
        "priority": 6,
        "module": "legal_brain",
        "created_at": now,
        "updated_at": now,
    }
    new_phases.append(new_phase_18E)
    print(f"  CREATED: 18E — Legal Brain Command Center")

    new_phase_18F = {
        "id": "18F",
        "title": "18F: Legal Brain Domain Plugin Architecture",
        "description": (
            "Design plugin interface for future non-Ghana jurisdictions: JSON schema for jurisdiction plugins, "
            "isolation guarantees (each domain = own DB + vector store + KG), domains disabled by default "
            "operator-activated, Ghana Legal Brain as reference implementation, stub interfaces for "
            "Medical/Family/Finance domains. Design only — implementation per-domain later."
        ),
        "status": "proposed",
        "dependencies": ["18C"],
        "priority": 20,
        "module": "legal_brain",
        "created_at": now,
        "updated_at": now,
    }
    new_phases.append(new_phase_18F)
    print(f"  CREATED: 18F — Legal Brain Domain Plugin Architecture (proposed)")

    # Step 9: Update the roadmap
    roadmap["phases"] = new_phases
    roadmap["updated_at"] = now

    # Recompute stats
    state_counts = {}
    for p in new_phases:
        s = p.get("status", "unknown")
        state_counts[s] = state_counts.get(s, 0) + 1

    print(f"\nFinal phase count: {len(new_phases)} (net change: {len(new_phases) - original_count})")
    print(f"State distribution: {state_counts}")

    result = {
        "original_count": original_count,
        "new_count": len(new_phases),
        "removed": removed_ids,
        "resurrected": ["18C", "19E", "19L", "19Q"],
        "created": ["18D", "18E", "18F"],
        "state_counts": state_counts,
    }

    with open(ROADMAP_PATH, "w") as f:
        json.dump(roadmap, f, indent=2)

    print("\n✓ roadmap.json updated successfully")
    return result

if __name__ == "__main__":
    main()
