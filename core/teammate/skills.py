"""Skill Registry (TF Phase 2 / 21B).

Persistent, globally-reusable registry of teammate skills. Each skill
describes what a teammate can do (capabilities, tools, model needs,
security constraints) so downstream phases (planner, execution) can pick a
capable teammate without coupling to a specific worker.

Persistence via ``core.memory``: memory/skills.json, schema v1.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

from core.memory import load as _memory_load, save as _memory_save

logger = logging.getLogger(__name__)

STORE = "skills.json"
SCHEMA_VERSION = 1

SEED_SKILL_IDS = [
    "inspect_repository", "inspect_service", "inspect_proxmox",
    "inspect_network", "inspect_opnsense", "inspect_database",
    "write_code", "run_tests", "run_security_scan",
    "benchmark_model", "deploy_service", "rollback_service",
    "verify_endpoint", "inspect_logs", "diagnose_failure",
]


@dataclass
class SkillRecord:
    skill_id: str
    name: str
    description: str
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    required_capabilities: list = field(default_factory=list)
    required_permissions: dict = field(default_factory=dict)
    allowed_tools: list = field(default_factory=list)
    forbidden_tools: list = field(default_factory=list)
    model_requirements: dict = field(default_factory=dict)
    security_requirements: dict = field(default_factory=dict)
    verification_method: str = "manual_review"
    timeout: int = 300
    resource_requirements: dict = field(default_factory=dict)
    version: int = 1
    dependencies: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SkillRecord":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


class SkillRegistry:
    """Source of truth for reusable skills. All mutations persist
    immediately to memory/skills.json."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillRecord] = {}
        self.load()

    # -- persistence ----------------------------------------------------
    def load(self) -> None:
        raw = _memory_load(STORE)
        if not isinstance(raw, dict) or "skills" not in raw:
            self._skills = {}
            return
        entries = raw.get("skills") or {}
        if not isinstance(entries, dict):
            self._skills = {}
            return
        loaded: dict[str, SkillRecord] = {}
        for sid, rec in entries.items():
            if not isinstance(rec, dict):
                continue
            try:
                loaded[sid] = SkillRecord.from_dict(rec)
            except Exception as e:
                logger.warning("skill %s corrupted, skipping: %s", sid, e)
                continue
        self._skills = loaded

    def save(self) -> None:
        _memory_save(STORE, {
            "schema_version": SCHEMA_VERSION,
            "skills": {sid: rec.to_dict() for sid, rec in self._skills.items()},
        })

    # -- mutations ------------------------------------------------------
    def register(self, skill: SkillRecord) -> SkillRecord:
        if skill.skill_id in self._skills:
            raise ValueError(f"skill already registered: {skill.skill_id}")
        self._skills[skill.skill_id] = skill
        self.save()
        return skill

    def upsert(self, skill: SkillRecord) -> SkillRecord:
        existing = self._skills.get(skill.skill_id)
        if existing is not None and existing.to_dict() != skill.to_dict():
            skill.version = existing.version + 1
        self._skills[skill.skill_id] = skill
        self.save()
        return skill

    def seed_default_15(self) -> int:
        """Idempotently seed the 15 canonical skills. Returns the number of
        NEW skills registered (0 when everything already exists)."""
        seeds = [
            SkillRecord(
                skill_id="inspect_repository",
                name="Inspect Repository",
                description="Survey a git repo layout, branches, and state.",
                inputs=["repo_path"],
                outputs=["report"],
                required_capabilities=["inspect", "repository"],
                required_permissions={"secrets": [], "network": [], "filesystem": ["repo"]},
                allowed_tools=["git", "ls", "find"],
                forbidden_tools=["write", "delete"],
                model_requirements={"capability": "generate", "roles": ["kai_brain"]},
                security_requirements={"level": "low", "read_only": True},
                verification_method="manual_review",
                timeout=300,
                resource_requirements={"cpu": 1, "memory": "512mb"},
            ),
            SkillRecord(
                skill_id="inspect_service",
                name="Inspect Service",
                description="Check a systemd/docker service's status and logs.",
                inputs=["service_name"],
                outputs=["status_report"],
                required_capabilities=["inspect", "service"],
                required_permissions={"secrets": [], "network": [], "filesystem": ["/var/log"]},
                allowed_tools=["systemctl", "docker", "journalctl"],
                forbidden_tools=["start", "stop", "restart", "deploy"],
                model_requirements={"capability": "generate", "roles": ["kai_brain"]},
                security_requirements={"level": "low", "read_only": True},
                verification_method="manual_review",
                timeout=300,
                resource_requirements={"cpu": 1, "memory": "512mb"},
            ),
            SkillRecord(
                skill_id="inspect_proxmox",
                name="Inspect Proxmox",
                description="Check Proxmox node/VM/LXC state via API.",
                inputs=["node", "resource"],
                outputs=["state_report"],
                required_capabilities=["inspect", "proxmox"],
                required_permissions={"secrets": [], "network": ["proxmox-api"], "filesystem": []},
                allowed_tools=["proxmox_api"],
                forbidden_tools=["migrate", "destroy", "stop"],
                model_requirements={"capability": "generate", "roles": ["kai_brain"]},
                security_requirements={"level": "low", "read_only": True},
                verification_method="manual_review",
                timeout=300,
                resource_requirements={"cpu": 1, "memory": "256mb"},
            ),
            SkillRecord(
                skill_id="inspect_network",
                name="Inspect Network",
                description="Survey network topology, routes, and reachability.",
                inputs=["targets"],
                outputs=["network_report"],
                required_capabilities=["inspect", "network"],
                required_permissions={"secrets": [], "network": ["lan", "tailscale"], "filesystem": []},
                allowed_tools=["ping", "traceroute", "ss", "ip"],
                forbidden_tools=["iptables", "route change"],
                model_requirements={"capability": "generate", "roles": ["kai_brain"]},
                security_requirements={"level": "low", "read_only": True},
                verification_method="manual_review",
                timeout=300,
                resource_requirements={"cpu": 1, "memory": "256mb"},
            ),
            SkillRecord(
                skill_id="inspect_opnsense",
                name="Inspect OPNsense",
                description="Check OPNsense firewall state and rules.",
                inputs=["target"],
                outputs=["firewall_report"],
                required_capabilities=["inspect", "opnsense"],
                required_permissions={"secrets": ["ai-orchestrator/opnsense"], "network": ["opnsense"], "filesystem": []},
                allowed_tools=["opnsense_api"],
                forbidden_tools=["commit", "apply"],
                model_requirements={"capability": "generate", "roles": ["kai_brain"]},
                security_requirements={"level": "low", "read_only": True},
                verification_method="manual_review",
                timeout=300,
                resource_requirements={"cpu": 1, "memory": "256mb"},
            ),
            SkillRecord(
                skill_id="inspect_database",
                name="Inspect Database",
                description="Check database schema, size, and query state.",
                inputs=["db_name"],
                outputs=["db_report"],
                required_capabilities=["inspect", "database"],
                required_permissions={"secrets": ["ai-orchestrator/databases"], "network": ["db"], "filesystem": []},
                allowed_tools=["sql_select", "psql", "mysql"],
                forbidden_tools=["sql_write", "drop", "truncate"],
                model_requirements={"capability": "generate", "roles": ["kai_brain"]},
                security_requirements={"level": "low", "read_only": True},
                verification_method="manual_review",
                timeout=300,
                resource_requirements={"cpu": 1, "memory": "256mb"},
            ),
            SkillRecord(
                skill_id="write_code",
                name="Write Code",
                description="Implement a code change in a bounded diff.",
                inputs=["task", "repo_path"],
                outputs=["diff", "commit"],
                required_capabilities=["code", "write"],
                required_permissions={"secrets": [], "network": [], "filesystem": ["repo", "sandbox"]},
                allowed_tools=["edit", "apply_patch", "generate"],
                forbidden_tools=["rm", "drop", "truncate", "destroy"],
                model_requirements={"capability": "coding", "roles": ["kai_coder"]},
                security_requirements={"level": "medium", "read_only": False},
                verification_method="tests_pass",
                timeout=600,
                resource_requirements={"cpu": 1, "memory": "1gb"},
            ),
            SkillRecord(
                skill_id="run_tests",
                name="Run Tests",
                description="Execute the repo's test suite and report results.",
                inputs=["repo_path", "test_command"],
                outputs=["test_report"],
                required_capabilities=["test", "code"],
                required_permissions={"secrets": [], "network": [], "filesystem": ["repo", "sandbox"]},
                allowed_tools=["pytest", "bash"],
                forbidden_tools=["rm -rf", "destroy"],
                model_requirements={"capability": "generate", "roles": ["kai_brain", "kai_coder"]},
                security_requirements={"level": "medium", "read_only": False},
                verification_method="results_report",
                timeout=600,
                resource_requirements={"cpu": 1, "memory": "1gb"},
            ),
            SkillRecord(
                skill_id="run_security_scan",
                name="Run Security Scan",
                description="Scan code/config for security regressions.",
                inputs=["repo_path"],
                outputs=["security_findings"],
                required_capabilities=["security", "inspect"],
                required_permissions={"secrets": [], "network": [], "filesystem": ["repo", "sandbox"]},
                allowed_tools=["scan", "grep", "bandit", "trivy"],
                forbidden_tools=["write", "deploy"],
                model_requirements={"capability": "security", "roles": ["kai_security"]},
                security_requirements={"level": "high", "read_only": True},
                verification_method="findings_review",
                timeout=1200,
                resource_requirements={"cpu": 1, "memory": "2gb"},
            ),
            SkillRecord(
                skill_id="benchmark_model",
                name="Benchmark Model",
                description="Run a token/quality benchmark against a model.",
                inputs=["model", "benchmark_set"],
                outputs=["benchmark_results"],
                required_capabilities=["benchmark"],
                required_permissions={"secrets": [], "network": ["model-apis"], "filesystem": []},
                allowed_tools=["curl", "model_invoke"],
                forbidden_tools=["modify_model"],
                model_requirements={"capability": "benchmark", "roles": ["kai_brain", "kai_coder"]},
                security_requirements={"level": "low", "read_only": True},
                verification_method="results_report",
                timeout=1800,
                resource_requirements={"cpu": 2, "memory": "4gb"},
            ),
            SkillRecord(
                skill_id="deploy_service",
                name="Deploy Service",
                description="Ship a verified build to a target environment.",
                inputs=["build_id", "environment"],
                outputs=["deploy_result"],
                required_capabilities=["deploy"],
                required_permissions={"secrets": [], "network": ["deploy-targets"], "filesystem": ["sandbox"]},
                allowed_tools=["deploy", "docker", "systemctl"],
                forbidden_tools=["rollback", "destroy"],
                model_requirements={"capability": "deploy", "roles": ["kai_brain"]},
                security_requirements={"level": "high", "read_only": False,
                                       "requires_approval": True},
                verification_method="verify_endpoint",
                timeout=1200,
                resource_requirements={"cpu": 1, "memory": "1gb"},
            ),
            SkillRecord(
                skill_id="rollback_service",
                name="Rollback Service",
                description="Revert a service to its previous known-good state.",
                inputs=["service_name", "from_build"],
                outputs=["rollback_result"],
                required_capabilities=["deploy", "rollback"],
                required_permissions={"secrets": [], "network": ["deploy-targets"], "filesystem": ["sandbox"]},
                allowed_tools=["rollback", "docker", "systemctl"],
                forbidden_tools=["destroy"],
                model_requirements={"capability": "deploy", "roles": ["kai_brain"]},
                security_requirements={"level": "critical", "read_only": False,
                                       "requires_approval": True},
                verification_method="verify_endpoint",
                timeout=1200,
                resource_requirements={"cpu": 1, "memory": "1gb"},
            ),
            SkillRecord(
                skill_id="verify_endpoint",
                name="Verify Endpoint",
                description="Confirm an HTTP endpoint responds as expected.",
                inputs=["url", "expected_status"],
                outputs=["verification_report"],
                required_capabilities=["verify", "network"],
                required_permissions={"secrets": [], "network": ["endpoints"], "filesystem": []},
                allowed_tools=["curl"],
                forbidden_tools=["write"],
                model_requirements={"capability": "generate", "roles": ["kai_brain"]},
                security_requirements={"level": "low", "read_only": True},
                verification_method="status_check",
                timeout=120,
                resource_requirements={"cpu": 1, "memory": "128mb"},
            ),
            SkillRecord(
                skill_id="inspect_logs",
                name="Inspect Logs",
                description="Read and analyze service/application logs.",
                inputs=["service_name", "since"],
                outputs=["log_analysis"],
                required_capabilities=["inspect", "logs"],
                required_permissions={"secrets": [], "network": [], "filesystem": ["/var/log"]},
                allowed_tools=["journalctl", "tail", "grep"],
                forbidden_tools=["write", "delete"],
                model_requirements={"capability": "generate", "roles": ["kai_brain"]},
                security_requirements={"level": "low", "read_only": True},
                verification_method="manual_review",
                timeout=300,
                resource_requirements={"cpu": 1, "memory": "256mb"},
            ),
            SkillRecord(
                skill_id="diagnose_failure",
                name="Diagnose Failure",
                description="Correlate symptoms + logs + health to root-cause a failure.",
                inputs=["incident_id", "symptoms"],
                outputs=["diagnosis"],
                required_capabilities=["diagnose", "inspect"],
                required_permissions={"secrets": [], "network": ["diagnostics"], "filesystem": ["/var/log"]},
                allowed_tools=["inspect", "correlate", "journalctl"],
                forbidden_tools=["remediate"],
                model_requirements={"capability": "generate", "roles": ["kai_brain"]},
                security_requirements={"level": "medium", "read_only": True},
                verification_method="manual_review",
                timeout=600,
                resource_requirements={"cpu": 1, "memory": "512mb"},
            ),
        ]
        assert [s.skill_id for s in seeds] == SEED_SKILL_IDS, \
            "seed set drifted from SEED_SKILL_IDS"
        created = 0
        for seed in seeds:
            if seed.skill_id not in self._skills:
                self._skills[seed.skill_id] = seed
                created += 1
        if created:
            self.save()
        return created

    # -- reads ----------------------------------------------------------
    def get(self, skill_id: str) -> Optional[SkillRecord]:
        return self._skills.get(skill_id)

    def list(self) -> list[SkillRecord]:
        return list(self._skills.values())

    def search_by_capability(self, capability: str) -> list[SkillRecord]:
        return [s for s in self._skills.values()
                if capability in (s.required_capabilities or [])]

    def search_by_tool(self, tool: str) -> list[SkillRecord]:
        return [s for s in self._skills.values()
                if tool in (s.allowed_tools or [])]

    def validate(self, skill_id: str) -> list[str]:
        skill = self._skills.get(skill_id)
        if skill is None:
            return [f"unknown skill_id: {skill_id}"]
        problems: list[str] = []
        if not skill.name:
            problems.append("name is required")
        if not skill.description:
            problems.append("description is required")
        if not skill.required_capabilities:
            problems.append("required_capabilities must not be empty")
        dupes = set(skill.allowed_tools or []) & set(skill.forbidden_tools or [])
        if dupes:
            problems.append(f"tools in both allowed and forbidden: {sorted(dupes)}")
        if not skill.model_requirements:
            problems.append("model_requirements must not be empty")
        if skill.timeout <= 0:
            problems.append("timeout must be positive")
        return problems
