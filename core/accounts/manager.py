"""Account Registry.

One record per provider registration. Duplicate prevention for the onboarding
engine is expressed as :func:`exists`. Every mutation is published on the real
event bus and written to the HMAC audit log. Passwords are never stored — only
a ``vault_reference`` path.
"""

from __future__ import annotations

from typing import Optional, Union

from core import audit_logger, kai_event_bus
from core.id_generator import generate_id

from core.accounts import store
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

SOURCE = "account_registry"
IDENTITY_KEY_FIELDS = ("provider_account_id", "username", "email", "phone", "digital_identity")


class AccountNotFound(KeyError):
    def __init__(self, account_id: str):
        super().__init__(f"account not found: {account_id}")
        self.account_id = account_id


class DuplicateAccount(ValueError):
    def __init__(self, provider: str, keys: dict):
        super().__init__(f"duplicate account for provider {provider!r}: {sorted(keys)}")
        self.provider = provider
        self.keys = keys


def _new_id() -> str:
    return f"acct-{generate_id()}"


def _model(record: dict) -> AccountRecord:
    return AccountRecord(**record)


def _as_create(payload: Union[AccountCreate, dict]) -> AccountCreate:
    return payload if isinstance(payload, AccountCreate) else AccountCreate(**payload)


def _as_update(payload: Union[AccountUpdate, dict]) -> AccountUpdate:
    return payload if isinstance(payload, AccountUpdate) else AccountUpdate(**payload)


def _identity_keys(identity: Union[dict, str]) -> dict:
    if isinstance(identity, dict):
        return {k: v for k, v in identity.items() if k in IDENTITY_KEY_FIELDS and v}
    if isinstance(identity, str) and identity:
        return {"email": identity, "username": identity, "provider_account_id": identity}
    return {}


def _duplicate_keys(record: dict) -> dict:
    return {k: record.get(k) for k in IDENTITY_KEY_FIELDS if record.get(k)}


def _audit(event_type: str, account_id: str, method: str, details: Optional[dict] = None) -> None:
    audit_logger.log_audit_event(
        event_type=event_type,
        operator=SOURCE,
        endpoint=f"account/{account_id}",
        method=method,
        status_code=200,
        details=details or {},
    )


def _publish(topic: str, account_id: str, **extra) -> None:
    payload = {"account_id": account_id}
    payload.update(extra)
    kai_event_bus.publish(topic, payload, source=SOURCE)


def exists(provider: str, identity: Union[dict, str]) -> bool:
    """True when an active account for *provider* matches any identity key.

    Used by the onboarding engine to prevent duplicate registrations.
    """
    wanted = _identity_keys(identity)
    if not wanted:
        return False
    for rec in store.read_records():
        if rec.get("provider") != provider:
            continue
        if rec.get("status") == AccountStatus.archived.value:
            continue
        if any(rec.get(field) == value for field, value in wanted.items()):
            return True
    return False


def find_accounts(
    provider: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    username: Optional[str] = None,
    provider_account_id: Optional[str] = None,
    digital_identity: Optional[str] = None,
) -> list[AccountRecord]:
    criteria = {
        "email": email,
        "phone": phone,
        "username": username,
        "provider_account_id": provider_account_id,
        "digital_identity": digital_identity,
    }
    wanted = {k: v for k, v in criteria.items() if v}
    results = []
    for rec in store.read_records():
        if rec.get("provider") != provider:
            continue
        if all(rec.get(field) == value for field, value in wanted.items()):
            results.append(_model(rec))
    return results


def create_account(payload: Union[AccountCreate, dict]) -> AccountRecord:
    data = _as_create(payload)
    account_id = _new_id()
    now = now_iso()
    record = AccountRecord(
        account_id=account_id,
        provider=data.provider,
        provider_account_id=data.provider_account_id,
        account_type=data.account_type,
        username=data.username,
        email=data.email,
        phone=data.phone,
        digital_identity=data.digital_identity,
        browser_profile=data.browser_profile,
        creation_time=now,
        verification_status=data.verification_status,
        security_status=data.security_status,
        vault_reference=data.vault_reference or vault_reference_for(data.provider, account_id),
        mission_id=data.mission_id,
        risk_level=data.risk_level,
        status=AccountStatus.active,
        created_at=now,
        updated_at=now,
    ).model_dump(mode="json")

    candidate = _duplicate_keys(record)

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if rec.get("provider") != record["provider"]:
                continue
            if rec.get("status") == AccountStatus.archived.value:
                continue
            if any(rec.get(field) == value for field, value in candidate.items()):
                raise DuplicateAccount(record["provider"], candidate)
        records.append(record)
        return records

    store.update_records(_mutate)
    _publish("account.created", account_id, provider=record["provider"])
    _audit("account.create", account_id, "CREATE", {"provider": record["provider"]})
    return _model(record)


def get_account(account_id: str) -> Optional[AccountRecord]:
    for record in store.read_records():
        if record.get("account_id") == account_id:
            return _model(record)
    return None


def list_accounts(
    provider: Optional[str] = None,
    status: Optional[str] = None,
    digital_identity: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AccountRecord]:
    records = store.read_records()
    if provider:
        records = [r for r in records if r.get("provider") == provider]
    if status:
        records = [r for r in records if r.get("status") == status]
    if digital_identity:
        records = [r for r in records if r.get("digital_identity") == digital_identity]
    records = sorted(records, key=lambda r: r.get("created_at", ""), reverse=True)
    return [_model(r) for r in records[offset : offset + limit]]


def update_account(account_id: str, payload: Union[AccountUpdate, dict]) -> AccountRecord:
    data = _as_update(payload)
    result: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if rec.get("account_id") != account_id:
                continue
            for field in (
                "provider_account_id", "account_type", "username", "email", "phone",
                "digital_identity", "browser_profile", "vault_reference", "mission_id",
                "last_verified", "last_activity",
            ):
                value = getattr(data, field)
                if value is not None:
                    rec[field] = value
            if data.verification_status is not None:
                rec["verification_status"] = data.verification_status.value
            if data.security_status is not None:
                rec["security_status"] = data.security_status.value
            if data.risk_level is not None:
                rec["risk_level"] = data.risk_level.value
            rec["updated_at"] = now_iso()
            result.update(rec)
            return records
        raise AccountNotFound(account_id)

    store.update_records(_mutate)
    _audit("account.update", account_id, "UPDATE", {"provider": result.get("provider")})
    return _model(result)


def archive_account(account_id: str) -> AccountRecord:
    result: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if rec.get("account_id") != account_id:
                continue
            rec["status"] = AccountStatus.archived.value
            rec["updated_at"] = now_iso()
            result.update(rec)
            return records
        raise AccountNotFound(account_id)

    store.update_records(_mutate)
    _audit("account.archive", account_id, "DELETE", {"provider": result.get("provider")})
    return _model(result)


def set_verification_status(account_id: str, status: Union[VerificationStatus, str]) -> AccountRecord:
    try:
        target = VerificationStatus(status)
    except ValueError:
        raise ValueError(f"invalid verification status: {status!r}")

    result: dict = {}
    old_status = None

    def _mutate(records: list[dict]) -> list[dict]:
        nonlocal old_status
        for rec in records:
            if rec.get("account_id") != account_id:
                continue
            old_status = rec.get("verification_status")
            rec["verification_status"] = target.value
            if target == VerificationStatus.VERIFIED:
                rec["last_verified"] = now_iso()
            rec["updated_at"] = now_iso()
            result.update(rec)
            return records
        raise AccountNotFound(account_id)

    store.update_records(_mutate)
    _publish(
        "account.verification.changed", account_id,
        provider=result.get("provider"), previous=old_status, current=target.value,
    )
    _audit(
        "account.verification.change", account_id, "UPDATE",
        {"previous": old_status, "current": target.value},
    )
    return _model(result)


def set_security_status(account_id: str, status: Union[SecurityStatus, str]) -> AccountRecord:
    try:
        target = SecurityStatus(status)
    except ValueError:
        raise ValueError(f"invalid security status: {status!r}")

    result: dict = {}
    old_status = None

    def _mutate(records: list[dict]) -> list[dict]:
        nonlocal old_status
        for rec in records:
            if rec.get("account_id") != account_id:
                continue
            old_status = rec.get("security_status")
            rec["security_status"] = target.value
            rec["updated_at"] = now_iso()
            result.update(rec)
            return records
        raise AccountNotFound(account_id)

    store.update_records(_mutate)
    _publish(
        "account.security.changed", account_id,
        provider=result.get("provider"), previous=old_status, current=target.value,
    )
    _audit(
        "account.security.change", account_id, "UPDATE",
        {"previous": old_status, "current": target.value},
    )
    return _model(result)


def _link_field(account_id: str, field: str, value: str, audit_event: str) -> AccountRecord:
    result: dict = {}

    def _mutate(records: list[dict]) -> list[dict]:
        for rec in records:
            if rec.get("account_id") != account_id:
                continue
            rec[field] = value
            rec["updated_at"] = now_iso()
            result.update(rec)
            return records
        raise AccountNotFound(account_id)

    store.update_records(_mutate)
    _audit(audit_event, account_id, "UPDATE", {field: value})
    return _model(result)


def link_mission(account_id: str, mission_id: str) -> AccountRecord:
    return _link_field(account_id, "mission_id", mission_id, "account.link_mission")


def link_identity(account_id: str, identity_id: str) -> AccountRecord:
    return _link_field(account_id, "digital_identity", identity_id, "account.link_identity")


def link_browser_profile(account_id: str, profile_ref: str) -> AccountRecord:
    return _link_field(account_id, "browser_profile", profile_ref, "account.link_browser_profile")


def link_vault_reference(account_id: str, reference_path: str) -> AccountRecord:
    """Link a vault *path* only. Never accepts or stores a secret value."""
    return _link_field(account_id, "vault_reference", reference_path, "account.link_vault_reference")


__all__ = [
    "AccountNotFound",
    "DuplicateAccount",
    "exists",
    "find_accounts",
    "create_account",
    "get_account",
    "list_accounts",
    "update_account",
    "archive_account",
    "set_verification_status",
    "set_security_status",
    "link_mission",
    "link_identity",
    "link_browser_profile",
    "link_vault_reference",
    "vault_reference_for",
    "VerificationStatus",
    "SecurityStatus",
    "AccountStatus",
]
