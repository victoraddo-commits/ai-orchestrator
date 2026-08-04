"""Phase 13I: Kai Future Roadmap Generator — creates new roadmap phases
from observations, build patterns, and operator conversations.

Analyzes recent build failures, chat history topics, and provider
performance to propose new roadmap phases that fill real gaps.
"""

import json
from datetime import datetime, timezone
from collections import Counter


def _analyze_failures():
    """Find recurring failure patterns that deserve a roadmap phase."""
    from core.memory import load

    builds = load("builds.json")
    if isinstance(builds, dict):
        builds = builds.get("records", [])
    if not builds:
        return []

    # Group failures by reason keyword
    keywords = Counter()
    for b in builds:
        if b.get("status") != "FAILED":
            continue
        reason = (b.get("failure_reason") or "").lower()
        if "no available provider" in reason:
            keywords["provider_fallback"] += 1
        elif "no changes" in reason or "no files" in reason or "no commits" in reason:
            keywords["zero_code_output"] += 1
        elif "quota" in reason or "429" in reason:
            keywords["quota_exhaustion"] += 1
        elif "timeout" in reason:
            keywords["timeout"] += 1
        elif "utf-8" in reason or "decode" in reason:
            keywords["encoding_error"] += 1
        else:
            keywords["other"] += 1

    proposals = []
    if keywords.get("zero_code_output", 0) >= 3:
        proposals.append({
            "id": "GEN-001",
            "title": "Fix gwen3 zero-code output pattern",
            "reason": f"{keywords['zero_code_output']} builds failed with no output",
            "suggested_priority": 5,
        })
    if keywords.get("provider_fallback", 0) >= 3:
        proposals.append({
            "id": "GEN-002",
            "title": "Provider fallback hardening",
            "reason": f"{keywords['provider_fallback']} builds failed with no available provider",
            "suggested_priority": 10,
        })
    if keywords.get("timeout", 0) >= 3:
        proposals.append({
            "id": "GEN-003",
            "title": "Coding agent timeout recovery",
            "reason": f"{keywords['timeout']} builds timed out",
            "suggested_priority": 15,
        })

    return proposals


def _analyze_chat():
    """Analyze recent chat for feature requests and recurring questions."""
    from core.kai.conversation import get_session

    envelope = get_session()
    messages = envelope.get("recent_messages", [])[-50:] if envelope else []

    # Simple keyword scanning
    topics = Counter()
    for msg in messages:
        content = (msg.get("content", "") or "").lower()
        for topic, keywords in {
            "susu_savings": ["susu", "saving", "contribution", "payout"],
            "mobile_money": ["momo", "mobile money", "mtn", "telecel"],
            "proxmox_backup": ["backup", "restore", "snapshot"],
            "monitoring_alert": ["alert", "monitor", "watch", "notify"],
            "security_harden": ["harden", "secure", "encrypt", "firewall"],
        }.items():
            if any(kw in content for kw in keywords):
                topics[topic] += 1

    proposals = []
    for topic, count in topics.most_common(3):
        if count >= 2:
            proposals.append({
                "id": f"GEN-CHAT-{len(proposals)+1:03d}",
                "title": topic.replace("_", " ").title(),
                "reason": f"Mentioned {count}x in recent chat",
                "suggested_priority": 50,
            })

    return proposals


def generate_roadmap_proposals():
    """Generate new roadmap phase proposals from system analysis."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "from_failures": _analyze_failures(),
        "from_chat": _analyze_chat(),
    }
