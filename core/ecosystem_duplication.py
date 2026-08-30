"""Duplication detection and report generation for the ecosystem graph.

Scans the current runtime graph for:
1. Multiple systems that send Telegram messages
2. Multiple secret storage systems
3. Multiple notification aggregators
4. Multiple JSON memory stores

Generates a markdown report and flags findings that need human review.
"""

from datetime import datetime, timezone
from core.ecosystem_graph import load_graph

# ── Detection rules ───────────────────────────────────────────────────────────

DUPLICATION_RULES = [
    {
        "id": "dup-telegram-001",
        "name": "Multiple Telegram Senders",
        "description": "More than one system independently sends Telegram messages",
        "check": "telegram_senders",
        "severity": "medium",
    },
    {
        "id": "dup-secrets-001",
        "name": "Multiple Secret Storage Systems",
        "description": "Secrets stored in more than one system",
        "check": "secret_stores",
        "severity": "high",
    },
    {
        "id": "dup-notify-001",
        "name": "Multiple Notification Aggregators",
        "description": "Multiple systems aggregate and forward notifications",
        "check": "notification_aggregators",
        "severity": "low",
    },
    {
        "id": "dup-memory-001",
        "name": "Multiple JSON Memory Stores",
        "description": "Multiple systems maintain their own JSON file stores",
        "check": "json_memory_stores",
        "severity": "low",
    },
]

def _get_telegram_senders(graph: dict) -> list[dict]:
    """Find all entities that send Telegram messages."""
    telegram_rels = [r for r in graph["relationships"] if "telegram" in r.get("type", "").lower()]
    senders = set(r["from"] for r in telegram_rels)
    return [{"id": sid, "entity": graph["entities"].get(sid, {})} for sid in senders]

def _get_secret_stores(graph: dict) -> list[dict]:
    """Find all entities that own secret storage."""
    secret_owners = [eid for eid, e in graph["entities"].items() if e.get("type") in ("secret_store", "capability_owner") or "secret" in e.get("id", "").lower()]
    return [{"id": sid, "entity": graph["entities"].get(sid, {})} for sid in secret_owners]

def _get_notification_aggregators(graph: dict) -> list[dict]:
    """Find all notification-capable entities."""
    notify_rels = [r for r in graph["relationships"] if "notif" in r.get("type", "").lower()]
    aggregators = set(r["from"] for r in notify_rels)
    return [{"id": sid, "entity": graph["entities"].get(sid, {})} for sid in aggregators]

def _get_json_memory_stores(graph: dict) -> list[dict]:
    """Find entities with JSON-based storage."""
    stores = []
    for eid, e in graph["entities"].items():
        if "path" not in e:
            continue
        path = e["path"]
        if any(x in path for x in ("memory", "json", "store")):
            stores.append({"id": eid, "entity": e})
    return stores

# ── Duplication detection ───────────────────────────────────────────────────────

CHECK_FUNCTIONS = {
    "telegram_senders": _get_telegram_senders,
    "secret_stores": _get_secret_stores,
    "notification_aggregators": _get_notification_aggregators,
    "json_memory_stores": _get_json_memory_stores,
}

def find_duplications() -> list[dict]:
    """Scan the current graph for duplications."""
    graph = load_graph()
    findings = []

    for rule in DUPLICATION_RULES:
        check_fn = CHECK_FUNCTIONS.get(rule["check"])
        if not check_fn:
            continue
        items = check_fn(graph)
        if len(items) > 1:
            findings.append({
                "id": rule["id"],
                "name": rule["name"],
                "description": rule["description"],
                "severity": rule["severity"],
                "systems": items,
                "count": len(items),
            })

    return findings

# ── Report generation ─────────────────────────────────────────────────────────

def generate_duplication_report() -> str:
    """Generate a human-readable markdown duplication report."""
    findings = find_duplications()
    now = datetime.now(timezone.utc).isoformat()

    lines = [
        "# KAI Ecosystem Duplication Report",
        "",
        f"**Generated:** {now}",
        f"**Status:** Initial Audit",
        "",
        "---",
        "",
        "## Summary",
        "",
    ]

    high = [f for f in findings if f["severity"] == "high"]
    medium = [f for f in findings if f["severity"] == "medium"]
    low = [f for f in findings if f["severity"] == "low"]

    lines.append(f"| Severity | Count |")
    lines.append(f"|----------|-------|")
    lines.append(f"| High     | {len(high)}     |")
    lines.append(f"| Medium   | {len(medium)}     |")
    lines.append(f"| Low      | {len(low)}      |")
    lines.append("")

    for severity_group, group_name in [(high, "High"), (medium, "Medium"), (low, "Low")]:
        if not severity_group:
            continue
        lines.append(f"### {group_name} Severity")
        lines.append("")
        for f in severity_group:
            lines.append(f"#### {f['name']} (`{f['id']}`)")
            lines.append("")
            lines.append(f"{f['description']}")
            lines.append("")
            if f["systems"]:
                lines.append("**Systems involved:**")
                for s in f["systems"]:
                    ename = s["entity"].get("name", s["id"])
                    lines.append(f"- `{s['id']}` — {ename}")
                lines.append("")
                lines.append("**Assessment:**")
                lines.append(f"{_assess_duplication(f)}")
                lines.append("")
                lines.append("**Recommendation:**")
                lines.append(f"{_recommend_action(f)}")
            else:
                lines.append(f"_{f.get('note', 'No systems found.')}_")
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)

def _assess_duplication(finding: dict) -> str:
    """Generate an assessment for a duplication finding."""
    count = finding["count"]
    if count == 0:
        return "No systems found for this capability — may indicate a gap."
    if count == 1:
        return "Only one system — no duplication."
    if finding["id"] == "dup-telegram-001":
        return "Complementary — different triggers and targets, but infrastructure overlaps. Telegram send logic is duplicated."
    if finding["id"] == "dup-secrets-001":
        return "Fragmented — secrets scattered across systems. kai-vault is canonical but locked. Consolidate into vault."
    if finding["id"] == "dup-notify-001":
        return "Complementary — kai-notify is general hub, kai-audit is observability-focused, telegra-approval-responder is approval-specific."
    return "Multiple systems — evaluate if consolidation would reduce complexity."

def _recommend_action(finding: dict) -> str:
    """Generate a recommendation for a duplication finding."""
    if finding["count"] == 0:
        return "N/A — no action needed."
    if finding["count"] == 1:
        return "N/A — no duplication."
    if finding["id"] == "dup-telegram-001":
        return "SPECIALIZE: Consolidate Telegram send infrastructure into kai-notify. telegra-approval-responder becomes a kai-notify source. kai-audit uses kai-notify for Telegram delivery."
    if finding["id"] == "dup-secrets-001":
        return "MERGE: Migrate all secrets to kai-vault once vault access is recovered. Deprecate orchestrator-secrets after migration."
    if finding["id"] == "dup-notify-001":
        return "PRESERVE: Systems serve different purposes. Ensure kai-notify is the canonical Telegram send path for all consumers."
    if finding["id"] == "dup-memory-001":
        return "OBSERVE: Monitor if JSON stores diverge. Consider unifying under Kai's memory layer if they grow."
    return "Evaluate per-system. Merge if same function, specialize if different function."
