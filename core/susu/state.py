"""FSM-based conversation state for the SUSU bot group-creation flow.

Stores ephemeral conversation state keyed by chat_id, tracking which step
of the group-creation wizard the user is in.
"""

from pathlib import Path

import core.memory as memory


STATE_FILE = "susu/conversations.json"


def _load_states():
    return memory.load(STATE_FILE, directory=Path("memory"))


def _save_states(data):
    memory.save(STATE_FILE, data, directory=Path("memory"))


def get_state(chat_id):
    states = _load_states()
    return states.get(chat_id)


def set_state(chat_id, state):
    states = _load_states()
    states[chat_id] = state
    _save_states(states)


def clear_state(chat_id):
    states = _load_states()
    states.pop(chat_id, None)
    _save_states(states)


def start_create_susu(chat_id):
    set_state(chat_id, {"step": "AWAITING_NAME", "data": {}})
    return "What should we call this SUSU group? Send me the name."


def handle_name(chat_id, text):
    state = get_state(chat_id)
    if not state or state["step"] != "AWAITING_NAME":
        return None

    state["data"]["name"] = text.strip()
    state["step"] = "AWAITING_AMOUNT"
    set_state(chat_id, state)
    return "Got it. What's the contribution amount and currency? (e.g. 50 USD)"


def handle_amount(chat_id, text):
    state = get_state(chat_id)
    if not state or state["step"] != "AWAITING_AMOUNT":
        return None

    parts = text.strip().split()
    if len(parts) < 2:
        return "Please send both an amount and a currency code, like: 50 USD"

    try:
        amount = float(parts[0])
    except (ValueError, TypeError):
        return "The amount must be a number. Try something like: 50 USD"

    currency = parts[1].upper()
    state["data"]["contribution_amount"] = amount
    state["data"]["currency"] = currency
    state["step"] = "AWAITING_FREQUENCY"
    set_state(chat_id, state)
    return "How often should contributions be made?"


def handle_frequency(chat_id, text):
    state = get_state(chat_id)
    if not state or state["step"] != "AWAITING_FREQUENCY":
        return None

    freq = text.strip().upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY"):
        return "Please choose DAILY, WEEKLY, or MONTHLY."

    state["data"]["frequency"] = freq
    state["step"] = "AWAITING_PARTICIPANTS"
    set_state(chat_id, state)
    return "How many participants (including yourself)?"


def handle_participants(chat_id, text):
    state = get_state(chat_id)
    if not state or state["step"] != "AWAITING_PARTICIPANTS":
        return None

    try:
        count = int(text.strip())
    except (ValueError, TypeError):
        return "Please send a number, like: 5"

    if count < 2:
        return "A SUSU group needs at least 2 participants."

    if count > 100:
        return "Maximum 100 participants per group."

    state["data"]["max_participants"] = count
    state["step"] = "AWAITING_FEE_TYPE"
    set_state(chat_id, state)
    return "Admin fee type: FLAT (fixed amount per cycle), PERCENTAGE (% of each contribution), or NONE?"


def handle_fee_type(chat_id, text):
    state = get_state(chat_id)
    if not state or state["step"] != "AWAITING_FEE_TYPE":
        return None

    fee_type = text.strip().upper()
    if fee_type not in ("FLAT", "PERCENTAGE", "NONE"):
        return "Please choose FLAT, PERCENTAGE, or NONE."

    state["data"]["admin_fee_type"] = fee_type

    if fee_type == "NONE":
        state["data"]["admin_fee_value"] = 0
        return _confirm(chat_id, state)

    state["step"] = "AWAITING_FEE_VALUE"
    set_state(chat_id, state)
    if fee_type == "FLAT":
        return "What's the flat fee amount per cycle? (e.g. 5)"
    else:
        return "What percentage of each contribution? (e.g. 5 for 5%)"


def handle_fee_value(chat_id, text):
    state = get_state(chat_id)
    if not state or state["step"] != "AWAITING_FEE_VALUE":
        return None

    try:
        value = float(text.strip())
    except (ValueError, TypeError):
        return "Please send a number for the fee."

    fee_type = state["data"].get("admin_fee_type", "")
    if fee_type == "PERCENTAGE" and (value <= 0 or value > 100):
        return "Percentage must be between 0.01 and 100."

    if value < 0:
        return "Fee cannot be negative."

    state["data"]["admin_fee_value"] = value
    return _confirm(chat_id, state)


def _confirm(chat_id, state):
    data = state["data"]
    fee_type = data.get("admin_fee_type", "NONE")
    fee_value = data.get("admin_fee_value", 0)

    fee_desc = "None"
    if fee_type == "FLAT":
        fee_desc = f"{fee_value} {data['currency']} per cycle"
    elif fee_type == "PERCENTAGE":
        fee_desc = f"{fee_value}% of each contribution"

    summary = (
        f"Confirm your SUSU group:\n\n"
        f"Name: {data['name']}\n"
        f"Contribution: {data['contribution_amount']} {data['currency']}\n"
        f"Frequency: {data['frequency']}\n"
        f"Participants: {data['max_participants']}\n"
        f"Admin Fee: {fee_desc}\n\n"
        f"Reply CONFIRM to create or CANCEL to start over."
    )

    state["step"] = "CONFIRMING"
    set_state(chat_id, state)
    return summary


def handle_confirmation(chat_id, text):
    state = get_state(chat_id)
    if not state or state["step"] != "CONFIRMING":
        return None

    cmd = text.strip().upper()
    if cmd == "CONFIRM":
        data = state["data"]
        clear_state(chat_id)
        return ("_create", {
            "name": data["name"],
            "contribution_amount": data["contribution_amount"],
            "currency": data["currency"],
            "frequency": data["frequency"],
            "max_participants": data["max_participants"],
            "admin_fee_type": data.get("admin_fee_type", "NONE"),
            "admin_fee_value": data.get("admin_fee_value", 0),
        })
    elif cmd == "CANCEL":
        clear_state(chat_id)
        return "Creation cancelled. Send /create_susu to start again."
    else:
        return "Please reply CONFIRM or CANCEL."


def start_deposit(chat_id, user_id):
    set_state(chat_id, {"step": "AWAITING_DEPOSIT_AMOUNT", "flow": "deposit", "data": {"user_id": user_id}})
    return "How much would you like to deposit? (in GHS)"


def handle_deposit_amount(chat_id, text):
    state = get_state(chat_id)
    if not state or state.get("step") != "AWAITING_DEPOSIT_AMOUNT":
        return None

    try:
        amount = float(text.strip())
    except (ValueError, TypeError):
        return "Please enter a valid amount (e.g. 50)"

    if amount <= 0:
        return "Amount must be greater than 0."

    state["data"]["amount"] = amount
    state["step"] = "AWAITING_DEPOSIT_PROVIDER"
    set_state(chat_id, state)
    return "Which mobile money provider? Choose: MTN, Telecel, or AirtelTigo"


def handle_deposit_provider(chat_id, text):
    state = get_state(chat_id)
    if not state or state.get("step") != "AWAITING_DEPOSIT_PROVIDER":
        return None

    provider_map = {
        "mtn": "mtn", "telecel": "telecel", "vodafone": "vodafone",
        "airteltigo": "airteltigo",
    }
    provider = provider_map.get(text.strip().lower())
    if not provider:
        return "Please choose: MTN, Telecel, or AirtelTigo"

    state["data"]["provider"] = provider
    state["step"] = "AWAITING_DEPOSIT_PHONE"
    set_state(chat_id, state)
    return "What is your mobile money phone number? (e.g. 0244123456)"


def handle_deposit_phone(chat_id, text):
    state = get_state(chat_id)
    if not state or state.get("step") != "AWAITING_DEPOSIT_PHONE":
        return None

    phone = text.strip()
    if not phone or len(phone) < 7:
        return "Please enter a valid phone number (at least 7 digits)."

    state["data"]["phone"] = phone
    state["step"] = "AWAITING_DEPOSIT_GROUP"
    set_state(chat_id, state)
    return "Is this for a specific SUSU group? Send the group ID, or reply NONE if this is just a wallet top-up."


def handle_deposit_group(chat_id, text):
    state = get_state(chat_id)
    if not state or state.get("step") != "AWAITING_DEPOSIT_GROUP":
        return None

    from core.susu import models

    answer = text.strip().upper()
    if answer == "NONE":
        return _execute_deposit(chat_id, state, None)

    group = models.get_group(text.strip())
    if not group:
        return f"Group {text.strip()} not found. Try again or reply NONE for wallet top-up."

    return _execute_deposit(chat_id, state, text.strip())


def _execute_deposit(chat_id, state, group_id):
    from core.susu import models

    data = state["data"]
    clear_state(chat_id)

    try:
        tx = models.deposit(
            user_id=state["data"].get("user_id", ""),
            amount=data["amount"],
            provider=data["provider"],
            phone=data["phone"],
            group_id=group_id,
            description=f"SUSU {'group '+group_id if group_id else 'wallet'} deposit",
        )
        provider_obj = get_provider(data["provider"])
        provider_name = provider_obj.provider_name() if provider_obj else data["provider"]
        fee_info = get_fee_info(data["amount"], data["provider"])

        lines = [
            "Deposit initiated!\n",
            f"Amount: GHS {data['amount']:.2f}",
            f"Provider: {provider_name}",
            f"Phone: {data['phone']}",
        ]
        if fee_info.get("success"):
            lines.append(f"Processor fee: GHS {fee_info['fee_amount']:.2f} ({fee_info['fee_percentage']}%)")
            lines.append(f"Net amount: GHS {fee_info['net_amount']:.2f}")
        if group_id:
            lines.append(f"Group: {group_id}")
        lines.append(f"\nStatus: {tx['status']}")
        lines.append(f"Reference: {tx['payment_id']}")

        if tx["status"] == "COMPLETED":
            lines.append("\nPayment completed!")
            balance = models.get_user_balance(tx["user_id"])
            lines.append(f"New balance: GHS {balance:.2f}")
        elif tx["status"] == "PROCESSING":
            lines.append("\nCheck your phone for a payment prompt from your mobile money provider.")

        return "\n".join(lines)
    except Exception as e:
        return f"Deposit failed: {type(e).__name__}: {e}"


def start_withdraw(chat_id, balance, user_id):
    set_state(chat_id, {"step": "AWAITING_WITHDRAW_AMOUNT", "flow": "withdraw", "data": {"balance": balance, "user_id": user_id}})
    return f"Your balance is GHS {balance:.2f}. How much would you like to withdraw?"


def handle_withdraw_amount(chat_id, text):
    state = get_state(chat_id)
    if not state or state.get("step") != "AWAITING_WITHDRAW_AMOUNT":
        return None

    try:
        amount = float(text.strip())
    except (ValueError, TypeError):
        return "Please enter a valid amount (e.g. 50)"

    if amount <= 0:
        return "Amount must be greater than 0."

    balance = state["data"].get("balance", 0)
    if amount > balance:
        return f"Insufficient balance. You have GHS {balance:.2f}."

    state["data"]["amount"] = amount
    state["step"] = "AWAITING_WITHDRAW_PROVIDER"
    set_state(chat_id, state)
    return "Which mobile money provider for withdrawal? Choose: MTN, Telecel, or AirtelTigo"


def handle_withdraw_provider(chat_id, text):
    state = get_state(chat_id)
    if not state or state.get("step") != "AWAITING_WITHDRAW_PROVIDER":
        return None

    provider_map = {
        "mtn": "mtn", "telecel": "telecel", "vodafone": "vodafone",
        "airteltigo": "airteltigo",
    }
    provider = provider_map.get(text.strip().lower())
    if not provider:
        return "Please choose: MTN, Telecel, or AirtelTigo"

    state["data"]["provider"] = provider
    state["step"] = "AWAITING_WITHDRAW_PHONE"
    set_state(chat_id, state)
    return "What is the mobile money number to send to? (e.g. 0244123456)"


def handle_withdraw_phone(chat_id, text):
    state = get_state(chat_id)
    if not state or state.get("step") != "AWAITING_WITHDRAW_PHONE":
        return None

    phone = text.strip()
    if not phone or len(phone) < 7:
        return "Please enter a valid phone number (at least 7 digits)."

    state["data"]["phone"] = phone
    state["step"] = "CONFIRMING_WITHDRAW"
    set_state(chat_id, state)

    data = state["data"]
    return (
        f"Confirm withdrawal:\n\n"
        f"Amount: GHS {data['amount']:.2f}\n"
        f"Provider: {data['provider']}\n"
        f"Phone: {data['phone']}\n\n"
        f"Reply CONFIRM to proceed or CANCEL to abort."
    )


def handle_withdraw_confirmation(chat_id, text):
    state = get_state(chat_id)
    if not state or state.get("step") != "CONFIRMING_WITHDRAW":
        return None

    cmd = text.strip().upper()
    if cmd == "CONFIRM":
        return _execute_withdraw(chat_id, state)
    elif cmd == "CANCEL":
        clear_state(chat_id)
        return "Withdrawal cancelled."
    else:
        return "Please reply CONFIRM or CANCEL."


def _execute_withdraw(chat_id, state):
    from core.susu import models

    data = state["data"]
    clear_state(chat_id)

    try:
        tx = models.withdraw(
            user_id=data.get("user_id", ""),
            amount=data["amount"],
            provider=data["provider"],
            phone=data["phone"],
            description="SUSU wallet withdrawal",
        )
        return (
            f"Withdrawal recorded!\n\n"
            f"Amount: GHS {data['amount']:.2f}\n"
            f"Provider: {data['provider']}\n"
            f"Phone: {data['phone']}\n"
            f"Reference: {tx['payment_id']}\n"
            f"Status: {tx['status']}"
        )
    except Exception as e:
        return f"Withdrawal failed: {type(e).__name__}: {e}"


def start_contribute(chat_id, group_id, amount, currency, user_id):
    set_state(chat_id, {
        "step": "AWAITING_CONTRIBUTE_PROVIDER",
        "flow": "contribute",
        "data": {"group_id": group_id, "amount": amount, "currency": currency, "user_id": user_id},
    })
    return (
        f"Contribute {amount} {currency} to group {group_id}.\n"
        f"Which mobile money provider? Choose: MTN, Telecel, or AirtelTigo"
    )


def handle_contribute_provider(chat_id, text):
    state = get_state(chat_id)
    if not state or state.get("step") != "AWAITING_CONTRIBUTE_PROVIDER":
        return None

    provider_map = {
        "mtn": "mtn", "telecel": "telecel", "vodafone": "vodafone",
        "airteltigo": "airteltigo",
    }
    provider = provider_map.get(text.strip().lower())
    if not provider:
        return "Please choose: MTN, Telecel, or AirtelTigo"

    state["data"]["provider"] = provider
    state["step"] = "AWAITING_CONTRIBUTE_PHONE"
    set_state(chat_id, state)
    return "What is your mobile money phone number? (e.g. 0244123456)"


def handle_contribute_phone(chat_id, text):
    state = get_state(chat_id)
    if not state or state.get("step") != "AWAITING_CONTRIBUTE_PHONE":
        return None

    phone = text.strip()
    if not phone or len(phone) < 7:
        return "Please enter a valid phone number (at least 7 digits)."

    state["data"]["phone"] = phone
    return _execute_contribute(chat_id, state)


def _execute_contribute(chat_id, state):
    from core.susu import models

    data = state["data"]
    clear_state(chat_id)

    try:
        tx = models.deposit(
            user_id=data.get("user_id", ""),
            amount=data["amount"],
            provider=data["provider"],
            phone=data["phone"],
            group_id=data["group_id"],
            description=f"Contribution to group {data['group_id']}",
        )
        provider_obj = get_provider(data["provider"])
        provider_name = provider_obj.provider_name() if provider_obj else data["provider"]

        return (
            f"Contribution payment initiated!\n\n"
            f"Amount: {data['amount']} {data['currency']}\n"
            f"Provider: {provider_name}\n"
            f"Group: {data['group_id']}\n"
            f"Reference: {tx['payment_id']}\n"
            f"Status: {tx['status']}\n\n"
            f"Check your phone for a payment prompt."
        )
    except Exception as e:
        return f"Contribution failed: {type(e).__name__}: {e}"


def get_provider(provider_code):
    from core.susu.mobile_money import get_provider as mm_get_provider
    return mm_get_provider(provider_code)


def get_fee_info(amount, provider):
    from core.susu.mobile_money import get_client as get_mm_client
    return get_mm_client().calculate_processor_fee(amount, provider)
