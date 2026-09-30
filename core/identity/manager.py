"""Digital Identity Manager.

Create/get/list/update/archive identities; link/unlink email, phone, domain
and browser profiles; resolve the default KAI identity. Every mutation is
published on the real event bus and written to the HMAC audit log. Credentials
are never stored — only a ``vault_namespace`` path.
"""

from __future__ import annotations

from typing import Optional, Union

from core import audit_logger, kai_event_bus
from core.id_generator import generate_id

from core.identity import store
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

SOURCE = "identity_manager"


class IdentityNotFound(KeyError):
    def __init__(self, identity_id: str):
        super().__init__(f"identity not found: {identity_id}")
        self.identity_id = identity_id


class DuplicateEmail(ValueError):
    def __init__(self, email: str):
        super().__init__(f"email already linked to another identity: {email}")
        self.email = email


def _new_id() -> str:
    return f"ident-{generate_id()}"


def _model(record: dict) -> DigitalIdentity:
    return DigitalIdentity(**record)


def _as_create(payload: Union[IdentityCreate, dict]) -> IdentityCreate:
    return payload if isinstance(payload, IdentityCreate) else IdentityCreate(**payload)


def _as_update(payload: Union[IdentityUpdate, dict]) -> IdentityUpdate:
    return payload if isinstance(payload, IdentityUpdate) else IdentityUpdate(**payload)


def _ensure_unique_email(records: list[dict], emails: list[str], exclude_id: Optional[str] = None) -> None:
    wanted = {e for e in emails if e}
    if not wanted:
        return
    for rec in records:
        if rec["identity_id"] == exclude_id:
            continue
        if rec.get("status") == IdentityStatus.archived.value:
            continue
        overlap = wanted & set(rec.get("emails") or [])
        if overlap:
            raise DuplicateEmail(sorted(overlap)[0])


def _audit(event_type: str, identity_id: str, method: str, details: Optional[dict] = None) -> None:
    audit_logger.log_audit_event(
        event_type=event_type,
        operator=SOURCE,
        endpoint=f"identity/{identity_id}",
        method=method,
        status_code=200,
        details=details or {},
    )


def _publish(topic: str, identity_id: str, **extra) -> None:
    payload = {"identity_id": identity_id}
    payload.update(extra)
    kai_event_bus.publish(topic, payload, source=SOURCE)


def create_identity(payload: Union[IdentityCreate, dict]) -> DigitalIdentity:
    data = _as_create(payload)
    identity_id = _new_id()
    now = now_iso()
    emails = [data.email] if data.email else []
    record = DigitalIdentity(
        identity_id=identity_id,
        display_name=data.display_name,
        email=data.email,
        emails=emails,
        phone=data.phone,
        phones=[data.phone] if data.phone else [],
        domain=data.domain,
        domains=[data.domain] if data.domain else [],
        browser_profiles=list(data.browser_profiles),
        vault_namespace=data.vault_namespace or identity_vault_namespace(identity_id),
        security_profile=data.security_profile,
        provider_permissions=dict(data.provider_permissions),
        recovery=data.recovery,
        is_default=data.is_default,
        status=IdentityStatus.active,
        created_at=now,
        updated_at=now,
    ).model_dump(mode="json")

    def _mutate(records: list[dict]) -> list[dict]:
        _ensure_unique_email(records, record.get("emails") or [])
        if record["is_default"]:
            for rec in records:
                rec["is_default"] = False
        records.append(record)
        return records

    store.update_records(_mutate)
    _publish("identity.created", identity_id, status=record["status"])
    _audit("identity.create", identity_id, "CREATE", {"display_name": record["display_name"]})
    return _model(record)


def get_identity(identity_id: str) -> Optional[DigitalIdentity]:
    for record in store.read_records():
        if record.get("identity_id") == identity_id:
            return _model(record)
    return None


def list_identities(
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[DigitalIdentity]:
    records = store.read_records()
    if status:
        records = [r for r in records if r.get("status") == status]
    if search:
        needle = search.lower()

        def _matches(rec: dict) -> bool:
            haystack = " ".join(
                str(rec.get(field) or "")
                for field in ("display_name", "email", "phone", "vault_namespace")
            )
            haystack += " " + " ".join(rec.get("emails") or []) + " " + " ".join(rec.get("domains") or [])
            return needle in haystack.lower()

        records = [r for r in records if _matches(r)]
    records = sorted(records, key=lambda r: r.get("created_at", ""), reverse=True)
    return [_model(r) for r in records[offset : offset + limit]]


def update_identity(identity_id: str, payload: Union[IdentityUpdate, dict]) -> DigitalIdentity:
    data = _as_update(payload)
    result: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if rec.get("identity_id") != identity_id:
                continue
            if data.display_name is not None:
                rec["display_name"] = data.display_name
            if data.security_profile is not None:
                rec["security_profile"] = data.security_profile.value
            if data.provider_permissions is not None:
                rec["provider_permissions"] = dict(data.provider_permissions)
            if data.recovery is not None:
                rec["recovery"] = data.recovery.model_dump(mode="json")
            if data.vault_namespace is not None:
                rec["vault_namespace"] = data.vault_namespace
            if data.status is not None:
                rec["status"] = data.status.value
            rec["updated_at"] = now_iso()
            result.update(rec)
            return records
        raise IdentityNotFound(identity_id)

    store.update_records(_mutate)
    _publish("identity.updated", identity_id, status=result.get("status"))
    _audit("identity.update", identity_id, "UPDATE", {"status": result.get("status")})
    return _model(result)


def archive_identity(identity_id: str) -> DigitalIdentity:
    return _update_field(identity_id, lambda rec, now: rec.update(
        status=IdentityStatus.archived.value, updated_at=now
    ), "identity.archive")


def set_default_identity(identity_id: str) -> DigitalIdentity:
    result: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        found = False
        for rec in records:
            if rec.get("identity_id") == identity_id:
                rec["is_default"] = True
                rec["updated_at"] = now_iso()
                result.update(rec)
                found = True
            else:
                rec["is_default"] = False
        if not found:
            raise IdentityNotFound(identity_id)
        return records

    store.update_records(_mutate)
    _publish("identity.updated", identity_id, is_default=True)
    _audit("identity.update", identity_id, "UPDATE", {"is_default": True})
    return _model(result)


def resolve_default_identity() -> Optional[DigitalIdentity]:
    for record in store.read_records():
        if record.get("is_default") and record.get("status") != IdentityStatus.archived.value:
            return _model(record)
    return None


def _update_field(identity_id: str, apply, audit_event: str) -> DigitalIdentity:
    result: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if rec.get("identity_id") != identity_id:
                continue
            apply(rec, now_iso())
            result.update(rec)
            return records
        raise IdentityNotFound(identity_id)

    store.update_records(_mutate)
    _publish("identity.updated", identity_id, status=result.get("status"))
    _audit(audit_event, identity_id, "UPDATE", {"status": result.get("status")})
    return _model(result)


# --- linkage -----------------------------------------------------------------


def link_email(identity_id: str, email: str) -> DigitalIdentity:
    validated = IdentityCreate(display_name="link", email=email).email
    result: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        _ensure_unique_email(records, [validated], exclude_id=identity_id)
        for rec in records:
            if rec.get("identity_id") != identity_id:
                continue
            if validated not in rec["emails"]:
                rec["emails"].append(validated)
            if not rec.get("email"):
                rec["email"] = validated
            rec["updated_at"] = now_iso()
            result.update(rec)
            return records
        raise IdentityNotFound(identity_id)

    store.update_records(_mutate)
    _publish("identity.updated", identity_id, linked_email=validated)
    _audit("identity.update", identity_id, "UPDATE", {"linked_email": validated})
    return _model(result)


def unlink_email(identity_id: str, email: str) -> DigitalIdentity:
    result: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if rec.get("identity_id") != identity_id:
                continue
            rec["emails"] = [e for e in rec["emails"] if e != email]
            if rec.get("email") == email:
                rec["email"] = rec["emails"][0] if rec["emails"] else None
            rec["updated_at"] = now_iso()
            result.update(rec)
            return records
        raise IdentityNotFound(identity_id)

    store.update_records(_mutate)
    _publish("identity.updated", identity_id, unlinked_email=email)
    _audit("identity.update", identity_id, "UPDATE", {"unlinked_email": email})
    return _model(result)


def link_phone(identity_id: str, phone: str) -> DigitalIdentity:
    return _link_list_field(identity_id, "phones", "phone", phone, "linked_phone")


def unlink_phone(identity_id: str, phone: str) -> DigitalIdentity:
    return _unlink_list_field(identity_id, "phones", "phone", phone, "unlinked_phone")


def link_domain(identity_id: str, domain: str) -> DigitalIdentity:
    return _link_list_field(identity_id, "domains", "domain", domain, "linked_domain")


def unlink_domain(identity_id: str, domain: str) -> DigitalIdentity:
    return _unlink_list_field(identity_id, "domains", "domain", domain, "unlinked_domain")


def link_browser_profile(identity_id: str, profile_ref: str) -> DigitalIdentity:
    return _link_list_field(identity_id, "browser_profiles", None, profile_ref, "linked_browser_profile")


def unlink_browser_profile(identity_id: str, profile_ref: str) -> DigitalIdentity:
    return _unlink_list_field(identity_id, "browser_profiles", None, profile_ref, "unlinked_browser_profile")


def _link_list_field(identity_id, list_field, primary_field, value, audit_key) -> DigitalIdentity:
    result: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if rec.get("identity_id") != identity_id:
                continue
            if value not in rec[list_field]:
                rec[list_field].append(value)
            if primary_field and not rec.get(primary_field):
                rec[primary_field] = value
            rec["updated_at"] = now_iso()
            result.update(rec)
            return records
        raise IdentityNotFound(identity_id)

    store.update_records(_mutate)
    _publish("identity.updated", identity_id, **{audit_key: value})
    _audit("identity.update", identity_id, "UPDATE", {audit_key: value})
    return _model(result)


def _unlink_list_field(identity_id, list_field, primary_field, value, audit_key) -> DigitalIdentity:
    result: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if rec.get("identity_id") != identity_id:
                continue
            rec[list_field] = [v for v in rec[list_field] if v != value]
            if primary_field and rec.get(primary_field) == value:
                rec[primary_field] = rec[list_field][0] if rec[list_field] else None
            rec["updated_at"] = now_iso()
            result.update(rec)
            return records
        raise IdentityNotFound(identity_id)

    store.update_records(_mutate)
    _publish("identity.updated", identity_id, **{audit_key: value})
    _audit("identity.update", identity_id, "UPDATE", {audit_key: value})
    return _model(result)


__all__ = [
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
    "SecurityProfile",
    "RecoveryConfig",
]
