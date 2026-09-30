"""ProviderAdapter interface — the uniform surface every provider integration
must expose to the onboarding engine.

The interface is deliberately *partially implementable*: only ``descriptor`` is
abstract. Every operation has a concrete default that raises
:class:`NotApplicableError` (a ``NotImplementedError``), so an adapter may
implement just the subset that is relevant — or explicitly return the
:data:`NOT_APPLICABLE` sentinel. The onboarding engine must call the policy
gate (``core.providers.gate.ensure_automation_allowed``) **before** invoking
any of these operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.providers.schema import ProviderDescriptor

#: Sentinel an adapter may return from an operation that does not apply to it.
NOT_APPLICABLE = "NOT_APPLICABLE"


class NotApplicableError(NotImplementedError):
    """Raised by default when an operation does not apply to an adapter."""

    def __init__(self, operation: str):
        super().__init__(f"operation not applicable for this provider: {operation}")
        self.operation = operation


class ProviderAdapter(ABC):
    """Uniform, partially-implementable adapter surface for a provider."""

    NOT_APPLICABLE = NOT_APPLICABLE

    @property
    @abstractmethod
    def descriptor(self) -> ProviderDescriptor:
        """The provider this adapter serves."""
        raise NotImplementedError

    # -- optional operations (partial implementations are valid) --------------
    def discovery(self, **kwargs):
        """Discover provider specifics / resolve ambiguity."""
        raise NotApplicableError("discovery")

    def registration(self, **kwargs):
        """Create a new account with the provider."""
        raise NotApplicableError("registration")

    def verification(self, **kwargs):
        """Complete account verification (email/phone/identity)."""
        raise NotApplicableError("verification")

    def authentication(self, **kwargs):
        """Authenticate an existing session."""
        raise NotApplicableError("authentication")

    def security_setup(self, **kwargs):
        """Configure MFA / security settings."""
        raise NotApplicableError("security_setup")

    def profile_setup(self, **kwargs):
        """Populate the account profile."""
        raise NotApplicableError("profile_setup")

    def recovery(self, **kwargs):
        """Configure account recovery."""
        raise NotApplicableError("recovery")

    def publishing(self, **kwargs):
        """Perform a provider-specific publishing action."""
        raise NotApplicableError("publishing")

    def account_status(self, **kwargs):
        """Report account status / health."""
        raise NotApplicableError("account_status")


#: Canonical operation names, in directive order.
OPERATIONS = (
    "discovery",
    "registration",
    "verification",
    "authentication",
    "security_setup",
    "profile_setup",
    "recovery",
    "publishing",
    "account_status",
)
