"""core.identity — Digital Identity Manager.

The keystone of Universal Account Registration: who KAI is online.

    from core.identity import create_identity, IdentityCreate

Credentials are never stored here; identities carry a vault *namespace* only.
"""

from core.identity.schema import (
    DigitalIdentity,
    IdentityCreate,
    IdentityStatus,
    IdentityUpdate,
    RecoveryConfig,
    SecurityProfile,
    identity_vault_namespace,
    now_iso,
)
from core.identity.manager import (
    DuplicateEmail,
    IdentityNotFound,
    archive_identity,
    create_identity,
    get_identity,
    link_browser_profile,
    link_domain,
    link_email,
    link_phone,
    list_identities,
    resolve_default_identity,
    set_default_identity,
    unlink_browser_profile,
    unlink_domain,
    unlink_email,
    unlink_phone,
    update_identity,
)

__all__ = [
    "DigitalIdentity",
    "IdentityCreate",
    "IdentityUpdate",
    "IdentityStatus",
    "SecurityProfile",
    "RecoveryConfig",
    "IdentityNotFound",
    "DuplicateEmail",
    "create_identity",
    "get_identity",
    "list_identities",
    "update_identity",
    "archive_identity",
    "set_default_identity",
    "resolve_default_identity",
    "link_email",
    "unlink_email",
    "link_phone",
    "unlink_phone",
    "link_domain",
    "unlink_domain",
    "link_browser_profile",
    "unlink_browser_profile",
    "identity_vault_namespace",
    "now_iso",
]
