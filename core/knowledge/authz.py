"""Knowledge Fabric — permission-aware retrieval scope (§14/§15).

Authorization happens BEFORE retrieval: given a principal and document ACLs,
return only documents the principal may READ. Deny-by-default. This is the
security-critical gate that keeps unauthorized chunks out of the model context.
"""
from __future__ import annotations

from .model import DocumentACL, Principal, Visibility


def _explicit(acl: DocumentACL) -> set:
    return set(acl.readers) | set(acl.editors) | set(acl.managers)


def can_read(principal: Principal, acl: DocumentACL) -> bool:
    uid = principal.user_id
    if acl.owner_user_id == uid:
        return True
    if principal.is_system_admin:
        return True
    if acl.visibility == Visibility.ADMIN_ONLY:
        return uid in acl.managers
    if uid in _explicit(acl):
        return True
    if acl.visibility == Visibility.GROUP:
        return bool(set(acl.group_readers) & set(principal.group_ids))
    if acl.visibility == Visibility.KNOWLEDGE_SPACE:
        return acl.space_id is not None and acl.space_id in set(principal.space_ids)
    # PRIVATE / SELECTED_USERS → only explicit grants (handled above)
    return False


def can_edit(principal: Principal, acl: DocumentACL) -> bool:
    uid = principal.user_id
    if principal.is_system_admin or acl.owner_user_id == uid:
        return True
    return uid in set(acl.editors) | set(acl.managers)


def can_manage(principal: Principal, acl: DocumentACL) -> bool:
    uid = principal.user_id
    if principal.is_system_admin or acl.owner_user_id == uid:
        return True
    return uid in set(acl.managers)


def filter_readable(principal: Principal, acls: list) -> list:
    """Return only the ACLs (documents) the principal may read.

    Retrieval callers must apply this BEFORE building model context / citations.
    """
    return [a for a in acls if can_read(principal, a)]


def readable_document_ids(principal: Principal, acls: list) -> set:
    return {a.document_id for a in filter_readable(principal, acls)}
