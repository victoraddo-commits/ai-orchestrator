"""Permissions + Vault + AgentGuard (TF Phase 8 / 21H).

Every teammate execution path flows through AgentGuard
(core/agentguard/guard.py) and capability-scoped vault access
(core/vault_client.py). Raw secrets never appear in prompts, logs,
audits, or reports; every vault access is recorded in
memory/secret_access_audit.json (via core.ai.secrets.record_object_access).

The guard FAILS CLOSED:
  - skill_id is required; a missing SkillRecord is a denial, never a
    downgrade to "no requirements".
  - Capability requirements come from the resolved record's
    required_capabilities, enforced against teammate.capabilities.
  - vault scope is the EXECUTING skill's required_permissions["secrets"]
    only — never a union across the teammate's other skills.
  - Secret values are fetched INSIDE the guard from pair-based grants,
    only for in-scope paths, and cleared on every exit path (fetch
    failure and malformed-grant paths included).
  - Stamps (security_history / audit_references) are persisted via
    mate_registry.save() exactly once per execute call when a registry is
    supplied — on success and deny paths alike.
  - repr()/as_public() of the execution context never expose values.

API (supersedes the pre-security-rework signature; see the design spec
Section 3 "API note"):

  execute(teammate, skill_id, action_type, resource, details, fn, *,
          secret_grants=None, mate_registry=None, fetch_secret=None)

    - skill_id: required positional; a missing/unknown SkillRecord denies
      (fail closed via GuardDenied), it is never treated as "no
      requirements".
    - capability requirements are derived INTERNALLY from the resolved
      SkillRecord.required_capabilities — callers do not pass a capability
      string.
    - secret_grants: a list of (vault_path, env_var) pairs to fetch. Only
      paths inside the executing skill's vault scope are fetched, and only
      via fetch_secret (the caller can never pre-fetch values past the
      guard). Default fetch_secret wraps core.vault_client.get_secret.
      Stale value-triples from the pre-rework plan raise a clear TypeError.
    - on ALLOW, fn(ctx) runs with the RedactionContext; secrets are cleared
      in a finally on every exit path; the guarding audit row is written
      BEFORE any security_history stamp so no audit row is ever dropped by
      a stamp failure.

  for_teammate(teammate, skill_registry) -> list[str]

    REPORTING ONLY: the union of vault paths a teammate could ever execute
    across skills it holds. This is never the injection scope.

  GuardDenied(decision, message, approval_request_id=None),
  RedactionContext(ctx): see below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core.agentguard.guard import ActionRequest, AgentGuard, Decision, ActionType


class GuardDenied(Exception):
    """Raised when AgentGuard denies (or requires approval for) an action.

    Attributes:
        decision: machine decision (deny / require_approval)
        approval_request_id: set when an approval was created upstream
        message: human-readable reason (contains no secret material)
    """

    def __init__(self, decision: str, message: str,
                 approval_request_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.decision = decision
        self.approval_request_id = approval_request_id
        self.message = message


@dataclass
class RedactionContext:
    """Execution context handed to ``fn``. Secrets live ONLY in the
    private ``secrets`` dict and are excluded from repr/public views."""

    teammate_id: str = ""
    skill_id: str = ""
    payload: dict = field(default_factory=dict, repr=False)
    secrets: dict = field(default_factory=dict, repr=False)

    def as_public(self) -> dict:
        """Public view — no secret values, only path keys."""
        return {
            "teammate_id": self.teammate_id,
            "skill_id": self.skill_id,
            "payload": self.payload,
            "secret_keys": sorted(self.secrets.keys()),
        }

    def __repr__(self) -> str:
        return f"RedactionContext({self.as_public()!r})"


class ExecutionGuard:
    """Gate every teammate execution through AgentGuard + vault scope.

    Signature (post security-rework)::

        execute(teammate, skill_id, action_type, resource, details, fn, *,
                secret_grants=None, mate_registry=None, fetch_secret=None)
    """

    def __init__(self, agent_guard: Optional[AgentGuard] = None,
                 skill_registry: Any = None) -> None:
        guard = agent_guard or AgentGuard({"enabled": True})
        if not bool(getattr(guard, "enabled", True)):
            raise ValueError("refusing a fail-open (disabled) AgentGuard")
        self._guard = guard
        self._skills = skill_registry

    def for_teammate(self, teammate: Any, skill_registry: Any) -> list[str]:
        """REPORTING ONLY — the union of vault paths a teammate could ever
        execute across the skills it holds. Never the injection boundary;
        execute() injects from the executing skill's scope only."""
        scopes: list[str] = []
        for sid in getattr(teammate, "skills", []) or []:
            skill = skill_registry.get(sid)
            if skill is None:
                continue
            perms = skill.required_permissions or {}
            scopes.extend(perms.get("secrets", []))
        return sorted(set(scopes))

    def _deny(self, decision: Decision, message: str,
              approval_request_id: Optional[str] = None) -> GuardDenied:
        return GuardDenied(decision.value, message, approval_request_id)

    def execute(self,
                teammate: Any,
                skill_id: str,
                action_type: ActionType,
                resource: str,
                details: str,
                fn: Callable[[RedactionContext], Any],
                *,
                secret_grants: Optional[list] = None,
                mate_registry: Any = None,
                fetch_secret: Optional[Callable[[str, str], Optional[str]]] = None,
                ) -> Any:
        """Run ``fn`` under the guard. Returns fn's result on ALLOW.

        ``secret_grants`` are ``(vault_path, env_var)`` pairs. Only paths in
        the executing skill's vault scope are fetched (via ``fetch_secret``)
        and injected. The default fetcher wraps core.vault_client.get_secret.

        Audit integrity: on every DENY path the denying audit row is written
        BEFORE the security_history stamp, so a corrupted (non-list)
        security_history that makes the stamp raise TypeError can never drop
        the deny record.

        Persistence: if behavior was written to the mate (security_history /
        audit_references) and a mate_registry was supplied, the registry's
        save() is called exactly once before returning or raising, so
        stamps survive a registry reload.

        Secret lifetime: ctx.secrets is cleared in a finally on EVERY exit
        path after fetching begins — fetch-failure, malformed-grant, and
        fn-failure paths included — never before fn runs.
        """
        mate = self._get_mate(teammate, mate_registry)
        stamped = False
        try:
            # 1. Resolve the skill record — fail closed, never downgrade.
            try:
                record = self._resolve_skill(skill_id)
            except GuardDenied as gd:
                self._audit_access(teammate, skill_id, "", "guarded_execution",
                                   False, detail="deny: unresolved skill")
                self._stamp_security(mate, "deny", gd.message)
                stamped = True
                raise

            # 2. Capability gate derived from the record, not a caller string.
            caps = getattr(teammate, "capabilities", []) or []
            missing = [c for c in (record.required_capabilities or [])
                       if c not in caps]
            if missing:
                reason = (f"teammate lacks required capabilities for "
                          f"{skill_id!r}: {sorted(missing)}")
                self._audit_access(teammate, skill_id, "", "guarded_execution",
                                   False, detail="deny: missing capability")
                self._stamp_security(mate, "deny", reason)
                stamped = True
                raise self._deny(Decision.DENY, reason)

            skills = getattr(teammate, "skills", []) or []
            if skill_id not in skills:
                reason = f"teammate not linked to skill {skill_id!r}"
                self._audit_access(teammate, skill_id, "", "guarded_execution",
                                   False, detail="deny: skill not linked")
                self._stamp_security(mate, "deny", reason)
                stamped = True
                raise self._deny(Decision.DENY, reason)

            # 3. AgentGuard decision.
            request = ActionRequest(
                agent_id=str(getattr(teammate, "id", "?")),
                user_id="teammate-factory",
                action_type=action_type,
                resource=resource,
                details=details,
                metadata={"skill_id": skill_id},
            )
            result = self._guard.check_action(request)
            if (result.decision == Decision.ALLOW
                    and self._record_requires_approval(record)):
                message = (f"skill {skill_id!r} requires approval for "
                           f"{action_type.value} on {resource}")
                self._audit_access(teammate, skill_id, "", "guarded_execution",
                                   False, detail="require_approval")
                self._stamp_security(mate, "require_approval", message)
                stamped = True
                raise self._deny(Decision.REQUIRE_APPROVAL, message)
            if result.decision != Decision.ALLOW:
                self._audit_access(teammate, skill_id, "", "guarded_execution",
                                   False, detail=result.decision.value)
                self._stamp_security(
                    mate, "deny", f"{result.decision.value}: {result.message}")
                stamped = True
                raise self._deny(result.decision, result.message,
                                 approval_request_id=result.approval_request_id)

            # 4. In-guard vault fetch (executing skill's scope only) + fn.
            #    ctx.secrets is cleared on EVERY exit path: fetch failure,
            #    malformed-grant unpack, fn failure, and success. A raising
            #    fetcher is audited with STATIC text only (never the
            #    exception message) and re-raised before fn runs — no fake
            #    "execution" row is written for a fn that never started.
            ctx = RedactionContext(
                teammate_id=str(getattr(teammate, "id", "?")),
                skill_id=skill_id,
            )
            scope = self._resolved_scope(record)
            fetcher = self._default_fetch if fetch_secret is None else fetch_secret
            fn_started = False
            try:
                for grant in (secret_grants or []):
                    try:
                        vault_path, env_var = grant
                    except (TypeError, ValueError):
                        raise TypeError(
                            "secret_grants entries must be (vault_path, "
                            f"env_var) pairs, got {grant!r}"
                        ) from None
                    if vault_path not in scope:
                        continue
                    try:
                        value = fetcher(vault_path, env_var)
                    except Exception:
                        self._audit_access(teammate, skill_id, vault_path,
                                           "vault.fetch", False,
                                           detail="fetch failed")
                        raise
                    if value is not None:
                        ctx.secrets[env_var] = value
                        self._audit_access(teammate, skill_id, vault_path,
                                           "vault.fetch", True)
                    else:
                        self._audit_access(teammate, skill_id, vault_path,
                                           "vault.fetch", False,
                                           detail="secret not found")
                fn_started = True
                result_value = fn(ctx)
            except Exception:
                if fn_started:
                    self._audit_access(teammate, skill_id, "", "execution",
                                       False, detail="exception")
                raise
            finally:
                ctx.secrets.clear()

            # 5. Stamp success — the allow audit row is written before any
            #    stamp that could fail, so it is never dropped.
            self._stamp_audit(mate, f"executed {skill_id}")
            self._audit_access(teammate, skill_id, "", "guarded_execution", True,
                               detail="allow")
            self._stamp_security(mate, "allow",
                                 f"allowed {action_type.value} on {resource}")
            stamped = True
            return result_value
        finally:
            if stamped and mate_registry is not None:
                mate_registry.save()

    # -- helpers --------------------------------------------------------
    def _resolve_skill(self, skill_id: str) -> Any:
        """Resolve the SkillRecord or fail CLOSED."""
        from core.teammate.skills import SkillRegistry
        if not skill_id:
            raise GuardDenied("deny", "skill record unresolved: missing skill_id")
        reg = self._skills or SkillRegistry()
        skill = reg.get(skill_id)
        if skill is None:
            raise GuardDenied("deny", f"skill record unresolved: {skill_id!r}")
        return skill

    def _resolved_scope(self, record: Any) -> set[str]:
        """Vault scope of the EXECUTING skill = its own declared secret
        paths. Never unions across the teammate's other skills."""
        return set((record.required_permissions or {}).get("secrets", []) or [])

    def _record_requires_approval(self, record: Any) -> bool:
        """Destructive/approval gating: a record whose seed carries
        security_requirements["requires_approval"] needs approval even for
        actions AgentGuard would otherwise ALLOW."""
        return bool((record.security_requirements or {}).get("requires_approval"))

    def _default_fetch(self, vault_path: str, env_var: str) -> Optional[str]:
        from core.vault_client import get_secret
        return get_secret(vault_path, env_var)

    def _get_mate(self, teammate: Any, mate_registry: Any) -> Any:
        if mate_registry is not None:
            return mate_registry.get(teammate.id) or teammate
        return teammate

    def _stamp_security(self, mate: Any, decision: str, reason: str) -> None:
        hist = getattr(mate, "security_history", None)
        if not isinstance(hist, list):
            raise TypeError(
                f"teammate.security_history must be a list to stamp, got "
                f"{type(hist).__name__}"
            )
        from datetime import datetime, timezone
        hist.append({
            "event": "guarded_execution",
            "decision": decision,
            "reason": reason[:300],
            "at": datetime.now(timezone.utc).isoformat(),
        })

    def _stamp_audit(self, mate: Any, event: str) -> None:
        refs = getattr(mate, "audit_references", None)
        if isinstance(refs, list):
            from datetime import datetime, timezone
            refs.append({"event": event,
                         "at": datetime.now(timezone.utc).isoformat()})

    @staticmethod
    def _audit_access(teammate: Any, skill_id: str, vault_path: str,
                      action: str, success: bool, detail: str = "") -> None:
        from core.ai.secrets import record_object_access
        record_object_access(
            getattr(teammate, "id", "?"), skill_id or "unknown", action, success,
            detail=f"{detail} vault_path={vault_path}",
        )
