"""AgentGuard - Core security implementation."""

from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ActionType(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    DESTRUCTIVE = "destructive"
    PRIVILEGED = "privileged"
    FINANCIAL = "financial"
    COMMUNICATION = "communication"
    CREDENTIAL = "credential"


class RiskLevel(str, Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class ActionRequest:
    """Request for action authorization."""
    agent_id: str
    user_id: str
    action_type: ActionType
    resource: str
    details: str
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardResult:
    """Result of authorization check."""
    decision: Decision
    risk_level: RiskLevel
    policy_matched: Optional[str] = None
    approval_required: bool = False
    approval_request_id: Optional[str] = None
    audit_event_id: Optional[str] = None
    message: Optional[str] = None


class AgentGuard:
    """
    Runtime security gate for autonomous agent actions.

    Every action flows through check_action() before execution.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        logger.info(f"AgentGuard initialized (enabled={self.enabled})")

    def check_action(self, request: ActionRequest) -> GuardResult:
        """
        Main entry point: evaluate if action should be allowed.

        Returns GuardResult with decision and metadata.
        """
        if not self.enabled:
            return GuardResult(
                decision=Decision.ALLOW,
                risk_level=RiskLevel.SAFE,
                message="AgentGuard disabled"
            )

        # 1. Classify risk
        risk = self._assess_risk(request)

        # 2. Evaluate policies
        decision, matched_policy = self._evaluate_policies(request, risk)

        # 3. Create audit event
        audit_id = self._log_audit_event(request, decision, matched_policy, risk)

        # 4. Create approval request if needed
        approval_id = None
        if decision == Decision.REQUIRE_APPROVAL:
            approval_id = self._create_approval_request(request, risk)

        result = GuardResult(
            decision=decision,
            risk_level=risk,
            policy_matched=matched_policy,
            approval_required=(decision == Decision.REQUIRE_APPROVAL),
            approval_request_id=approval_id,
            audit_event_id=audit_id,
            message=self._decision_message(decision, risk, matched_policy)
        )

        logger.info(
            f"AgentGuard decision: {decision.value} "
            f"(risk={risk.value}, agent={request.agent_id}, "
            f"action={request.action_type.value}, resource={request.resource})"
        )

        return result

    def _assess_risk(self, request: ActionRequest) -> RiskLevel:
        """Classify action risk level."""
        action = request.action_type
        resource = request.resource.lower()
        details = request.details.lower()

        # CRITICAL risk patterns
        if action == ActionType.PRIVILEGED:
            return RiskLevel.CRITICAL
        if action == ActionType.DESTRUCTIVE and "production" in resource:
            return RiskLevel.CRITICAL
        if action == ActionType.FINANCIAL:
            return RiskLevel.CRITICAL
        if "sudo" in details or "rm -rf" in details:
            return RiskLevel.CRITICAL

        # HIGH risk patterns
        if action == ActionType.DESTRUCTIVE:
            return RiskLevel.HIGH
        if action == ActionType.EXECUTE and ("rm " in details or "delete" in details):
            return RiskLevel.HIGH
        if action == ActionType.COMMUNICATION:
            return RiskLevel.HIGH
        if action == ActionType.CREDENTIAL:
            return RiskLevel.HIGH

        # MEDIUM risk patterns
        if action == ActionType.WRITE and not resource.startswith("/tmp"):
            return RiskLevel.MEDIUM
        if action == ActionType.NETWORK and not (
            resource.startswith("http://localhost") or
            resource.startswith("http://127.0.0.1")
        ):
            return RiskLevel.MEDIUM
        if action == ActionType.EXECUTE:
            return RiskLevel.MEDIUM

        # LOW risk patterns
        if action == ActionType.WRITE and resource.startswith("/tmp"):
            return RiskLevel.LOW
        if action == ActionType.READ and not "secret" in resource:
            return RiskLevel.LOW

        # Default: SAFE
        return RiskLevel.SAFE

    def _evaluate_policies(self, request: ActionRequest, risk: RiskLevel) -> tuple:
        """
        Evaluate policies against request.
        Returns (decision, matched_policy_id).
        """
        # Default policy: CRITICAL always requires approval
        if risk == RiskLevel.CRITICAL:
            return Decision.REQUIRE_APPROVAL, "default-critical"

        # HIGH risk requires approval
        if risk == RiskLevel.HIGH:
            return Decision.REQUIRE_APPROVAL, "default-high"

        # MEDIUM: allow with audit
        if risk == RiskLevel.MEDIUM:
            return Decision.ALLOW, "default-medium"

        # LOW and SAFE: allow
        return Decision.ALLOW, "default-allow"

    def _log_audit_event(
        self,
        request: ActionRequest,
        decision: Decision,
        policy: Optional[str],
        risk: RiskLevel
    ) -> str:
        """Record audit event."""
        event_id = f"audit_{int(datetime.utcnow().timestamp() * 1000)}"

        # TODO: Write to audit log database when available
        logger.info(
            f"AgentGuard audit: {event_id} | "
            f"agent={request.agent_id} | "
            f"user={request.user_id} | "
            f"action={request.action_type.value} | "
            f"resource={request.resource} | "
            f"decision={decision.value} | "
            f"risk={risk.value} | "
            f"policy={policy}"
        )

        return event_id

    def _create_approval_request(self, request: ActionRequest, risk: RiskLevel) -> str:
        """Create approval request for human review."""
        approval_id = f"approval_{int(datetime.utcnow().timestamp() * 1000)}"

        # TODO: Write to approval queue when available
        # TODO: Notify user via Telegram/Command Center
        logger.warning(
            f"AgentGuard approval required: {approval_id} | "
            f"agent={request.agent_id} | "
            f"action={request.action_type.value} | "
            f"resource={request.resource} | "
            f"risk={risk.value} | "
            f"details={request.details[:100]}"
        )

        return approval_id

    def _decision_message(self, decision: Decision, risk: RiskLevel, policy: Optional[str]) -> str:
        """Generate human-readable decision message."""
        if decision == Decision.ALLOW:
            return f"Action allowed (risk: {risk.value}, policy: {policy})"
        elif decision == Decision.DENY:
            return f"Action denied (risk: {risk.value}, policy: {policy})"
        else:
            return f"Action requires approval (risk: {risk.value}, policy: {policy})"
