"""core.providers — Provider Registry + Adapter interface + ToS/legality gate.

    from core.providers import discover, get_provider, policy_gate
    from core.providers import ensure_automation_allowed  # blocking precondition

Descriptors are code-registered reference data; human-confirmed policy
overrides persist under ``memory/provider_overrides.json``. Importing this
package registers the shipped stub adapter and the STEP 7 Amazon adapter.
"""

from core.providers.schema import (
    AutomationPolicy,
    GateDecision,
    GateOutcome,
    ProviderDescriptor,
    now_iso,
)
from core.providers.adapter import (
    NOT_APPLICABLE,
    NotApplicableError,
    OPERATIONS,
    ProviderAdapter,
)
from core.providers.gate import (
    AutomationBlocked,
    HumanConfirmationRequired,
    ensure_automation_allowed,
    policy_gate,
)
from core.providers.manager import (
    AdapterNotFound,
    DuplicateAdapter,
    DuplicateProvider,
    PolicyOverridePending,
    ProviderNotFound,
    authorize_provider_action,
    discover,
    get_adapter,
    get_provider,
    get_ready_adapter,
    list_adapters,
    list_providers,
    register_adapter,
    register_provider,
    reset_registry,
    set_policy_override,
)
from core.providers import examples  # noqa: F401  (registers shipped stub adapter)
from core.providers import amazon  # noqa: F401  (STEP 7 real provider)
from core.providers.amazon import AmazonAdapter  # noqa: F401

amazon.register_amazon()

__all__ = [
    # schema
    "AutomationPolicy",
    "GateOutcome",
    "GateDecision",
    "ProviderDescriptor",
    "now_iso",
    # adapter
    "ProviderAdapter",
    "NotApplicableError",
    "NOT_APPLICABLE",
    "OPERATIONS",
    # gate
    "policy_gate",
    "ensure_automation_allowed",
    "AutomationBlocked",
    "HumanConfirmationRequired",
    # registry / manager
    "ProviderNotFound",
    "DuplicateProvider",
    "DuplicateAdapter",
    "AdapterNotFound",
    "PolicyOverridePending",
    "register_provider",
    "get_provider",
    "list_providers",
    "discover",
    "register_adapter",
    "get_adapter",
    "list_adapters",
    "set_policy_override",
    "authorize_provider_action",
    "get_ready_adapter",
    "reset_registry",
    # amazon (STEP 7)
    "AmazonAdapter",
]
