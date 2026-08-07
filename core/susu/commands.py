"""SUSU bot command handlers -- pure logic, no Telegram I/O.

Command handlers return a string reply or a special tuple ("_create", data)
to signal group creation. The bot loop handles the actual persistence and
message sending.
"""

from core.susu import models, state


HELP_TEXT = """SUSU Bot - Group Savings Made Easy

SUSU Commands:
/create_susu - Start a new SUSU group (interactive wizard)
/my_groups - List your SUSU groups
/group <id> - View a group's details
/pay_fee <fee_id> - Pay a pending fee
/my_fees - View your pending fees

Mobile Money:
/deposit - Deposit money via mobile money (interactive)
/balance - Check your wallet balance
/withdraw - Withdraw from your wallet
/transactions - View your transaction history
/help - Show this message"""


def cmd_help():
    return HELP_TEXT


def cmd_create_susu(chat_id, user_id, username, first_name):
    models.upsert_user(user_id, username=username, first_name=first_name)
    return state.start_create_susu(chat_id)


def cmd_my_groups(user_id):
    models.upsert_user(user_id)
    all_groups = models.list_groups()
    my_groups = [g for g in all_groups
                 if any(m["user_id"] == user_id
                        for m in models.get_members(g["id"]))]

    if not my_groups:
        return "You don't have any SUSU groups yet. Send /create_susu to start one."

    lines = ["Your SUSU groups:"]
    for g in my_groups:
        members = models.get_members(g["id"])
        lines.append(
            f"\nID: {g['id']}\n"
            f"Name: {g['name']}\n"
            f"Contribution: {g['contribution_amount']} {g['currency']} ({g['frequency']})\n"
            f"Members: {len(members)}/{g['max_participants']} - Status: {g['status']}"
        )
    return "\n".join(lines)


def cmd_group_detail(group_id):
    group = models.get_group(group_id)
    if not group:
        return f"Group {group_id} not found."

    members = models.get_members(group_id)
    fees = models.get_fees_for_group(group_id)

    member_lines = []
    for m in members:
        user = models.get_user(m["user_id"])
        name = (user.get("first_name") or user.get("username") or m["user_id"]) if user else m["user_id"]
        member_lines.append(f"  Slot {m['slot_number']}: {name}")

    fee_desc = "None"
    if group["admin_fee_type"] != "NONE":
        fee_desc = f"{group['admin_fee_type']}: {group['admin_fee_value']}"
        if group["admin_fee_type"] == "PERCENTAGE":
            fee_desc += "%"

    pending_fees = [f for f in fees if f["status"] == "PENDING"]
    paid_fees = [f for f in fees if f["status"] == "PAID"]

    lines = [
        f"Group: {group['name']} ({group['id']})",
        f"Contribution: {group['contribution_amount']} {group['currency']}",
        f"Frequency: {group['frequency']}",
        f"Participants: {len(members)}/{group['max_participants']}",
        f"Admin Fee: {fee_desc}",
        f"Status: {group['status']}",
        f"Created: {group['created_at']}",
        "",
        "Members:",
    ] + member_lines + [
        "",
        f"Fee Ledger: {len(paid_fees)} paid, {len(pending_fees)} pending",
    ]

    if pending_fees:
        lines.append("\nPending fees:")
        for f in pending_fees:
            user = models.get_user(f["user_id"])
            name = (user.get("first_name") or user.get("username") or f["user_id"]) if user else f["user_id"]
            lines.append(f"  {f['id']}: {f['amount']} {group['currency']} ({f['fee_type']}) - owed by {name}")

    return "\n".join(lines)


def cmd_pay_fee(user_id, fee_id):
    fee = models.pay_fee(fee_id)
    if not fee:
        return f"Fee {fee_id} not found."

    group = models.get_group(fee["group_id"])
    if not group:
        return "Associated group not found."

    return f"Fee {fee['id']} ({fee['amount']} {group['currency']}, {fee['fee_type']}) marked as PAID."


def cmd_my_fees(user_id):
    fees = models.get_fees_for_user(user_id)
    if not fees:
        return "You have no fees."

    pending = [f for f in fees if f["status"] == "PENDING"]
    paid = [f for f in fees if f["status"] == "PAID"]

    lines = [f"Your fees: {len(paid)} paid, {len(pending)} pending"]

    if pending:
        lines.append("\nPending:")
        for f in pending:
            group = models.get_group(f["group_id"])
            group_name = group["name"] if group else f["group_id"]
            currency = group["currency"] if group else "N/A"
            lines.append(f"  {f['id']}: {f['amount']} {currency} ({f['fee_type']}) - Group: {group_name}")
            lines.append(f"    Pay with: /pay_fee {f['id']}")

    if paid:
        lines.append("\nPaid:")
        for f in paid:
            group = models.get_group(f["group_id"])
            group_name = group["name"] if group else f["group_id"]
            currency = group["currency"] if group else "N/A"
            lines.append(f"  {f['id']}: {f['amount']} {currency} ({f['fee_type']}) - Group: {group_name}")

    return "\n".join(lines)


def cmd_start():
    return "Welcome to the SUSU Bot! Send /create_susu to start a group, or /help to see all commands."


def cmd_deposit(chat_id, user_id, username, first_name):
    models.upsert_user(user_id, username=username, first_name=first_name)
    return state.start_deposit(chat_id, user_id)


def cmd_balance(user_id):
    models.upsert_user(user_id)
    balance = models.get_user_balance(user_id)
    return f"Your wallet balance: GHS {balance:.2f}"


def cmd_withdraw(chat_id, user_id, username, first_name):
    models.upsert_user(user_id, username=username, first_name=first_name)
    balance = models.get_user_balance(user_id)
    if balance <= 0:
        return "Your wallet balance is GHS 0.00. Nothing to withdraw."
    return state.start_withdraw(chat_id, balance, user_id)"


def cmd_transactions(user_id):
    models.upsert_user(user_id)
    txs = models.list_transactions_for_user(user_id, limit=10)
    if not txs:
        return "No transactions yet. Use /deposit to add money to your wallet."

    lines = [f"Recent transactions ({len(txs)}):"]
    for tx in txs:
        amount = float(tx["amount"])
        net = float(tx.get("net_amount", amount))
        symbol = "+" if tx["tx_type"] == "DEPOSIT" else "-"
        status_icon = {"COMPLETED": "OK", "PROCESSING": "...", "PENDING": "...", "FAILED": "FAIL"}.get(tx["status"], tx["status"])
        lines.append(
            f"  [{status_icon}] {symbol}GHS {amount:.2f} {'(net: ' + str(net) + ')' if net != amount else ''} "
            f"- {tx['description'] or tx['tx_type']} ({tx['provider']})"
        )
    return "\n".join(lines)


def cmd_contribute(chat_id, user_id, username, first_name, arg):
    models.upsert_user(user_id, username=username, first_name=first_name)
    if not arg:
        return "Usage: /contribute <group_id>"
    group = models.get_group(arg)
    if not group:
        return f"Group {arg} not found."
    return state.start_contribute(chat_id, arg, float(group["contribution_amount"]), group["currency"], user_id)
