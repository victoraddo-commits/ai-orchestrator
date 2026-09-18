"""§37 MODULE INTEGRATION — capability catalog for KAI modules.

This is the single declarative mapping from a KAI module + capability to the
teammate specialization and reusable skills KAI's unified workforce needs. The
catalog does NOT create an agent framework: it only names the capability, and
:mod:`core.integration.module_bridge` routes every request through the one
teammate Factory / Skill Registry / mission engine.

Adding a module here is a data change (declare capability -> specialization +
skills); adding a skill here is a data change too. No orchestration logic lives
in a module.
"""
from __future__ import annotations


def _skill(skill_id: str, name: str, description: str, capabilities: list[str],
           tools: list[str], risk: str = "low") -> dict:
    """Build a SkillRecord field dict for a module capability skill."""
    return {
        "skill_id": skill_id,
        "name": name,
        "description": description,
        "inputs": [],
        "outputs": ["report"],
        "required_capabilities": list(capabilities),
        "required_permissions": {"secrets": [], "network": ["module-apis"],
                                 "filesystem": []},
        "allowed_tools": list(tools),
        "forbidden_tools": ["rm", "delete", "destroy", "truncate"],
        "model_requirements": {"capability": "generate",
                               "roles": ["kai_brain", "local"]},
        "security_requirements": {"level": risk, "read_only": True},
        "verification_method": "manual_review",
        "timeout": 300,
        "resource_requirements": {"cpu": 1, "memory": "256mb"},
    }


# Module-provided skills registered into the one teammate Skill Registry.
MODULE_SKILLS: dict[str, dict] = {
    "legal_research": _skill(
        "legal_research", "Legal Research",
        "Research Ghanaian legal doctrine, statutes and precedent for a module.",
        ["legal_research", "inspect"], ["legal_brain", "klaus"]),
    "legal_document_analysis": _skill(
        "legal_document_analysis", "Legal Document Analysis",
        "Parse, summarise and cite legal documents for a module.",
        ["legal_document", "inspect"], ["legal_brain", "citation_parser"]),
    "legal_knowledge_ops": _skill(
        "legal_knowledge_ops", "Legal Knowledge Operations",
        "Query and verify the zero-trust legal corpus (WORM chain, citations).",
        ["legal_knowledge", "inspect"], ["legal_brain"]),
    "legal_corpus_ops": _skill(
        "legal_corpus_ops", "Legal Corpus Curation",
        "Discover, ingest and index legal sources for the Ghana corpus.",
        ["legal_curation", "inspect"], ["klaus"]),
    "financial_ops": _skill(
        "financial_ops", "Financial Operations",
        "Reconcile ledgers, budgets and mobile-money transactions.",
        ["financial_ops", "inspect"], ["money_api"]),
    "group_savings_ops": _skill(
        "group_savings_ops", "Group Savings Operations",
        "Reconcile SUSU/ROSCA groups, contributions and payouts.",
        ["group_savings", "inspect"], ["susu_api"]),
    "market_analysis": _skill(
        "market_analysis", "Market Analysis",
        "Analyse odds, markets and value edges for the betting module.",
        ["market_analysis", "inspect"], ["odds_api"]),
    "infra_diagnostics": _skill(
        "infra_diagnostics", "Infrastructure Diagnostics",
        "Diagnose services, containers and hosts for an infrastructure module.",
        ["infra", "diagnose", "inspect"], ["proxmox_api", "docker"]),
    "proxmox_ops": _skill(
        "proxmox_ops", "Proxmox Operations",
        "Inspect Proxmox nodes, VMs and LXC containers (read-only).",
        ["proxmox", "inspect"], ["proxmox_api"]),
    "airdrop_research": _skill(
        "airdrop_research", "Airdrop Research",
        "Research airdrop eligibility, risk and task requirements.",
        ["airdrop", "research", "inspect"], ["web"]),
    "app_build_review": _skill(
        "app_build_review", "App Build Review",
        "Review an Android/app build pipeline and its artifacts (read-only).",
        ["app_build", "inspect"], ["gradle", "adb"]),
    "network_diagnostics": _skill(
        "network_diagnostics", "Network Diagnostics",
        "Diagnose KAI-NET reachability, routes and tunnels (read-only).",
        ["network", "diagnose", "inspect"], ["ip", "ping", "tailscale"]),
    "command_center_ops": _skill(
        "command_center_ops", "Command Center Operations",
        "Coordinate Command Center panels, approvals and module health.",
        ["ops", "inspect"], ["command_center"]),
    "telegram_ops": _skill(
        "telegram_ops", "Telegram Operations",
        "Operate Telegram users, groups, policies and activity reporting.",
        ["telegram", "inspect"], ["telegram_bot"]),
    "provider_ops": _skill(
        "provider_ops", "Provider Operations",
        "Inspect provider health, quota, routing priority and failover state.",
        ["provider", "inspect"], ["ai_router"]),
    "build_pipeline_ops": _skill(
        "build_pipeline_ops", "Build Pipeline Operations",
        "Track application builds, approvals and deployment state.",
        ["build_mgmt", "inspect"], ["build_manager"]),
    "roadmap_ops": _skill(
        "roadmap_ops", "Roadmap Operations",
        "Evaluate phases, priorities and autonomous roadmap execution.",
        ["roadmap", "planning", "inspect"], ["roadmap"]),
    "approval_ops": _skill(
        "approval_ops", "Approval Operations",
        "Prepare human-gate approval decisions and security reviews.",
        ["approval", "inspect"], ["approval_center"]),
    "conversation_ops": _skill(
        "conversation_ops", "Conversation Operations",
        "Resolve operator chat intents and build-request extraction.",
        ["conversation", "inspect"], ["kai_chat"]),
}


def _module(specialization: str, expertise: str, skills: list[str],
            capabilities: list[str]) -> dict:
    return {
        "specialization": specialization,
        "expertise": expertise,
        "skills": list(skills),
        "capabilities": list(capabilities),
    }


# §37 named modules (plus the other self-registered Command Center modules).
MODULE_CATALOG: dict[str, dict] = {
    "juris-kai": _module(
        "legal_researcher", "legal research",
        ["legal_research", "legal_document_analysis"],
        ["legal-ai", "legal_research", "document-analysis",
         "subscription-management", "payment-processing", "referral-system",
         "user-management"]),
    "legal-brain": _module(
        "legal_knowledge_engineer", "legal knowledge",
        ["legal_knowledge_ops"],
        ["legal-knowledge", "document-storage", "citation-parsing",
         "vector-search", "sandboxed-processing"]),
    "klaus": _module(
        "legal_curator", "legal corpus curation",
        ["legal_corpus_ops"],
        ["legal_research", "document_ingestion", "citation_extraction",
         "embeddings"]),
    "kai-money": _module(
        "finance_analyst", "financial operations",
        ["financial_ops"],
        ["money-management", "ledger-reconciliation", "payment-processing"]),
    "susu": _module(
        "savings_operator", "group savings operations",
        ["group_savings_ops"],
        ["group-savings", "mobile-money", "payment-processing",
         "contribution-tracking"]),
    "kai-betting": _module(
        "betting_analyst", "market analysis",
        ["market_analysis"],
        ["betting", "odds-analysis", "market-analysis", "risk-management"]),
    "it-manager": _module(
        "infra_engineer", "infrastructure engineering",
        ["infra_diagnostics"],
        ["infrastructure", "service-health", "deployment", "monitoring"]),
    "proxdash": _module(
        "proxmox_operator", "proxmox operations",
        ["proxmox_ops"],
        ["proxmox", "vm-management", "container-management", "monitoring"]),
    "airdrop-hunter": _module(
        "airdrop_researcher", "airdrop research",
        ["airdrop_research"],
        ["airdrop-research", "eligibility-analysis", "risk-analysis"]),
    "android-factory": _module(
        "app_build_engineer", "application build",
        ["app_build_review"],
        ["android", "app-build", "apk", "ci-cd"]),
    "kai-net": _module(
        "network_engineer", "network engineering",
        ["network_diagnostics"],
        ["network", "reachability", "tunnel-management", "vpn"]),
    "command-center": _module(
        "ops_coordinator", "operations coordination",
        ["command_center_ops"],
        ["command-center", "ops-coordination", "dashboard"]),
    "telegram-manager": _module(
        "telegram_operator", "telegram operations",
        ["telegram_ops"],
        ["telegram-management", "user-management", "activity-tracking",
         "access-control"]),
    "ai-workforce": _module(
        "provider_operator", "provider operations",
        ["provider_ops"],
        ["provider-management", "health-monitoring", "workforce-visualization"]),
    "build-queue": _module(
        "build_operator", "build operations",
        ["build_pipeline_ops"],
        ["build-management", "application-builder", "deployment"]),
    "roadmap-engine": _module(
        "roadmap_planner", "roadmap planning",
        ["roadmap_ops"],
        ["roadmap-tracking", "autonomous-planning", "phase-management"]),
    "approval-center": _module(
        "approval_officer", "approval governance",
        ["approval_ops"],
        ["approval-management", "human-gate", "security-review"]),
    "kai-chat": _module(
        "conversation_agent", "conversation operations",
        ["conversation_ops"],
        ["chat", "command-dispatch", "conversation-memory"]),
}


def normalize_module(name: str) -> str:
    return (name or "").strip().lower().replace("_", "-")


def module_names() -> list[str]:
    return sorted(MODULE_CATALOG.keys())


def catalog_entry(module: str) -> dict | None:
    return MODULE_CATALOG.get(normalize_module(module))


def synthesize_entry(module: str, descriptor: dict) -> dict:
    """Build a catalog entry for a descriptor-only (self-registered) module.

    The module exposes its declared capabilities; KAI synthesizes one skill and
    one specialization so the module still consumes the unified workforce
    instead of building its own agent path.
    """
    slug = normalize_module(module).replace("-", "_")
    skill_id = f"{slug}_ops"
    entry = _module(
        f"{slug}_operator", f"{normalize_module(module)} operations",
        [skill_id], list(descriptor.get("capabilities") or []))
    entry["synthesized"] = True
    entry["skill"] = _skill(
        skill_id, f"{module} Operations",
        f"Operate the {module} module capability requested from KAI's workforce.",
        [f"{slug}_ops", "inspect"], ["module_api"])
    return entry
