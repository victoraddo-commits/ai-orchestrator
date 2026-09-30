"""core.accounts — Account Registry.

The keystone of Universal Account Registration: which provider registrations
KAI holds, linked to a digital identity and a vault *reference*.

    from core.accounts import create_account, AccountCreate, exists
"""

from core.accounts.schema import (
    AccountCreate,
    AccountRecord,
    AccountStatus,
    AccountUpdate,
    SecurityStatus,
    VerificationStatus,
    now_iso,
    vault_reference_for,
)
from core.accounts.manager import (
    AccountNotFound,
    DuplicateAccount,
    archive_account,
    create_account,
    exists,
    find_accounts,
    get_account,
    link_browser_profile,
    link_identity,
    link_mission,
    link_vault_reference,
    list_accounts,
    set_security_status,
    set_verification_status,
    update_account,
)

__all__ = [
    "AccountRecord",
    "AccountCreate",
    "AccountUpdate",
    "AccountStatus",
    "VerificationStatus",
    "SecurityStatus",
    "AccountNotFound",
    "DuplicateAccount",
    "create_account",
    "get_account",
    "list_accounts",
    "update_account",
    "archive_account",
    "find_accounts",
    "exists",
    "set_verification_status",
    "set_security_status",
    "link_mission",
    "link_identity",
    "link_browser_profile",
    "link_vault_reference",
    "vault_reference_for",
    "now_iso",
]
