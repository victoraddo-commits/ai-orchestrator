"""SUSU bot data models and persistence.

Uses core.memory for atomic persistence. Each entity type lives in
memory/susu/<entity>.json. The memory module handles atomic writes, backups,
and schema versioning automatically.
"""

import uuid
from datetime import datetime
from pathlib import Path

import core.memory as memory


DATA_DIR = Path("memory") / "susu"


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load(entity, default=None):
    if default is None:
        default = {}
    _ensure_dir()
    return memory.load(f"susu/{entity}.json", directory=Path("memory"))


def _save(entity, data):
    _ensure_dir()
    memory.save(f"susu/{entity}.json", data, directory=Path("memory"))


def _now():
    return datetime.utcnow().isoformat() + "Z"


def upsert_user(telegram_id, username=None, first_name=None):
    users = _load("users", default={"index": {}, "list": []})
    index = users.get("index", {})

    user = index.get(telegram_id)
    if user is None:
        user = {
            "id": telegram_id,
            "telegram_id": telegram_id,
            "username": username or "",
            "first_name": first_name or "",
            "created_at": _now(),
        }
        index[telegram_id] = user
        users = {"index": index, "list": list(index.values())}
        _save("users", users)

    if username and user.get("username") != username:
        user["username"] = username
    if first_name and user.get("first_name") != first_name:
        user["first_name"] = first_name

    return user


def get_user(telegram_id):
    users = _load("users", default={"index": {}, "list": []})
    return users.get("index", {}).get(telegram_id)


def create_group(creator_id, name, contribution_amount, currency, frequency,
                 max_participants, admin_fee_type, admin_fee_value):
    groups = _load("groups", default={"index": {}, "list": []})
    index = groups.get("index", {})

    group_id = str(uuid.uuid4())[:8]
    group = {
        "id": group_id,
        "name": name,
        "creator_id": creator_id,
        "contribution_amount": str(contribution_amount),
        "currency": currency,
        "frequency": frequency,
        "max_participants": int(max_participants),
        "admin_fee_type": admin_fee_type,
        "admin_fee_value": str(admin_fee_value),
        "status": "ACTIVE",
        "created_at": _now(),
    }
    index[group_id] = group
    groups = {"index": index, "list": list(index.values())}
    _save("groups", groups)

    add_member(group_id, creator_id, 1)

    if admin_fee_type in ("FLAT", "PERCENTAGE"):
        log_fee(group_id, creator_id, admin_fee_value, "CREATION")

    return group


def get_group(group_id):
    groups = _load("groups", default={"index": {}, "list": []})
    return groups.get("index", {}).get(group_id)


def list_groups():
    groups = _load("groups", default={"index": {}, "list": []})
    return groups.get("list", [])


def add_member(group_id, user_id, slot_number):
    members = _load("members", default={"by_group": {}, "by_user": {}})
    by_group = members.get("by_group", {})

    if group_id not in by_group:
        by_group[group_id] = []

    for m in by_group[group_id]:
        if m["user_id"] == user_id:
            return m

    member = {
        "id": str(uuid.uuid4())[:8],
        "group_id": group_id,
        "user_id": user_id,
        "slot_number": slot_number,
        "joined_at": _now(),
    }
    by_group[group_id].append(member)

    by_user = members.get("by_user", {})
    if user_id not in by_user:
        by_user[user_id] = []
    by_user[user_id].append(group_id)

    _save("members", {"by_group": by_group, "by_user": by_user})
    return member


def get_members(group_id):
    members = _load("members", default={"by_group": {}, "by_user": {}})
    by_group = members.get("by_group", {})
    return by_group.get(group_id, [])


def log_fee(group_id, user_id, amount, fee_type):
    fees = _load("fees", default={"index": {}, "list": []})
    index = fees.get("index", {})

    fee_id = str(uuid.uuid4())[:8]
    fee = {
        "id": fee_id,
        "group_id": group_id,
        "user_id": user_id,
        "amount": str(amount),
        "fee_type": fee_type,
        "status": "PENDING",
        "created_at": _now(),
    }
    index[fee_id] = fee
    fees = {"index": index, "list": list(index.values())}
    _save("fees", fees)
    return fee


def get_fees_for_group(group_id):
    fees = _load("fees", default={"index": {}, "list": []})
    return [f for f in fees.get("list", []) if f["group_id"] == group_id]


def get_fees_for_user(user_id):
    fees = _load("fees", default={"index": {}, "list": []})
    return [f for f in fees.get("list", []) if f["user_id"] == user_id]


def pay_fee(fee_id):
    fees = _load("fees", default={"index": {}, "list": []})
    index = fees.get("index", {})

    if fee_id in index:
        index[fee_id]["status"] = "PAID"
        fees = {"index": index, "list": list(index.values())}
        _save("fees", fees)
        return index[fee_id]
    return None


def create_transaction(user_id, group_id=None, tx_type="DEPOSIT", amount=0.0,
                       currency="GHS", provider="mtn", phone="", payer_name="",
                       description="", processor_fee=0.0, metadata=None):
    transactions = _load("transactions", default={"index": {}, "list": []})
    index = transactions.get("index", {})

    tx_id = str(uuid.uuid4())[:8]
    payment_id = f"SUSU-{tx_id}"
    transaction = {
        "id": tx_id,
        "payment_id": payment_id,
        "user_id": user_id,
        "group_id": group_id,
        "tx_type": tx_type,
        "amount": str(amount),
        "currency": currency,
        "provider": provider,
        "phone": phone,
        "payer_name": payer_name,
        "description": description,
        "processor_fee": str(processor_fee),
        "net_amount": str(round(amount - processor_fee, 2)),
        "status": "PENDING",
        "hubtel_transaction_id": "",
        "created_at": _now(),
        "completed_at": None,
        "metadata": metadata or {},
    }
    index[tx_id] = transaction
    transactions = {"index": index, "list": list(index.values())}
    _save("transactions", transactions)
    return transaction


def get_transaction(tx_id):
    transactions = _load("transactions", default={"index": {}, "list": []})
    return transactions.get("index", {}).get(tx_id)


def update_transaction_status(tx_id, status, hubtel_transaction_id=None):
    transactions = _load("transactions", default={"index": {}, "list": []})
    index = transactions.get("index", {})

    if tx_id in index:
        index[tx_id]["status"] = status
        if hubtel_transaction_id:
            index[tx_id]["hubtel_transaction_id"] = hubtel_transaction_id
        if status == "COMPLETED":
            index[tx_id]["completed_at"] = _now()
        transactions = {"index": index, "list": list(index.values())}
        _save("transactions", transactions)
        return index[tx_id]
    return None


def list_transactions_for_user(user_id, status=None, limit=50):
    transactions = _load("transactions", default={"index": {}, "list": []})
    result = [t for t in transactions.get("list", []) if t["user_id"] == user_id]
    if status:
        result = [t for t in result if t["status"] == status]
    result.sort(key=lambda t: t["created_at"], reverse=True)
    return result[:limit]


def list_transactions_for_group(group_id, status=None):
    transactions = _load("transactions", default={"index": {}, "list": []})
    result = [t for t in transactions.get("list", []) if t["group_id"] == group_id]
    if status:
        result = [t for t in result if t["status"] == status]
    result.sort(key=lambda t: t["created_at"], reverse=True)
    return result


def get_user_balance(user_id):
    transactions = _load("transactions", default={"index": {}, "list": []})
    deposits = sum(
        float(t["net_amount"])
        for t in transactions.get("list", [])
        if t["user_id"] == user_id and t["tx_type"] == "DEPOSIT" and t["status"] == "COMPLETED"
    )
    withdrawals = sum(
        float(t["amount"])
        for t in transactions.get("list", [])
        if t["user_id"] == user_id and t["tx_type"] == "WITHDRAWAL" and t["status"] == "COMPLETED"
    )
    return round(deposits - withdrawals, 2)


def get_processor_fees_summary(group_id=None, provider=None):
    transactions = _load("transactions", default={"index": {}, "list": []})
    tx_list = transactions.get("list", [])
    if group_id:
        tx_list = [t for t in tx_list if t["group_id"] == group_id]
    if provider:
        tx_list = [t for t in tx_list if t["provider"] == provider]

    by_provider = {}
    total_fees = 0.0
    for t in tx_list:
        fee = float(t.get("processor_fee", 0))
        prov = t.get("provider", "unknown")
        by_provider[prov] = by_provider.get(prov, 0.0) + fee
        total_fees += fee

    return {
        "total_processor_fees": round(total_fees, 2),
        "fees_by_provider": {k: round(v, 2) for k, v in by_provider.items()},
        "transaction_count": len(tx_list),
    }


def deposit(user_id, amount, provider, phone, group_id=None,
            description="", payer_name="", metadata=None):
    from core.susu.mobile_money import get_client as get_mm_client
    mm_client = get_mm_client()
    fee_data = mm_client.calculate_processor_fee(amount, provider)
    processor_fee = fee_data.get("fee_amount", 0.0) if fee_data.get("success") else 0.0

    tx = create_transaction(
        user_id=user_id,
        group_id=group_id,
        tx_type="DEPOSIT",
        amount=amount,
        provider=provider,
        phone=phone,
        payer_name=payer_name,
        description=description,
        processor_fee=processor_fee,
        metadata=metadata,
    )

    payment_result = mm_client.request_payment(
        amount=amount,
        phone=phone,
        provider=provider,
        description=description or f"SUSU deposit: {tx['payment_id']}",
        payment_id=tx["payment_id"],
        payer_name=payer_name,
    )

    if payment_result.get("success"):
        update_transaction_status(
            tx["id"],
            "COMPLETED" if payment_result.get("status") == "completed" else "PROCESSING",
            hubtel_transaction_id=payment_result.get("transaction_id"),
        )
    else:
        update_transaction_status(tx["id"], "FAILED")

    return get_transaction(tx["id"])


def withdraw(user_id, amount, provider, phone, group_id=None,
             description="", payer_name="", metadata=None):
    tx = create_transaction(
        user_id=user_id,
        group_id=group_id,
        tx_type="WITHDRAWAL",
        amount=amount,
        provider=provider,
        phone=phone,
        payer_name=payer_name,
        description=description,
        metadata=metadata,
    )
    return tx


def reconcile_transaction(tx_id):
    tx = get_transaction(tx_id)
    if not tx:
        return None

    hubtel_id = tx.get("hubtel_transaction_id")
    if not hubtel_id:
        return tx

    from core.susu.mobile_money import get_client as get_mm_client
    mm_client = get_mm_client()
    status_result = mm_client.check_payment_status(hubtel_id)

    if status_result.get("success"):
        new_status = "COMPLETED" if status_result.get("status") == "completed" else "PROCESSING"
        if new_status != tx["status"]:
            update_transaction_status(tx_id, new_status)
            return get_transaction(tx_id)

    return tx


def _clear_for_test():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for entity in ("users", "groups", "members", "fees", "transactions"):
        memory.save(f"susu/{entity}.json", {}, directory=Path("memory"))
