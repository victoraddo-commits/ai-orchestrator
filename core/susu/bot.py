"""SUSU bot: polling loop, command dispatch, and FSM-based conversations.

This bot handles group-based savings (SUSU/ROSCA) operations:
- Interactive group creation wizard (FSM-driven)
- Group listing and detail views
- Admin/Platform fee tracking and payment
"""

import os
import time

from dotenv import load_dotenv

load_dotenv()

from core.susu import telegram_client as tg
from core.susu import commands, models, state

POLL_INTERVAL_SECONDS = 3

_HELP_TEXT = commands.HELP_TEXT

_COMMAND_HANDLERS = {
    "/start": lambda chat_id, user_id, username, first_name, arg: commands.cmd_start(),
    "/help": lambda chat_id, user_id, username, first_name, arg: _HELP_TEXT,
    "/create_susu": lambda chat_id, user_id, username, first_name, arg: commands.cmd_create_susu(
        chat_id, user_id, username, first_name),
    "/my_groups": lambda chat_id, user_id, username, first_name, arg: commands.cmd_my_groups(user_id),
    "/group": lambda chat_id, user_id, username, first_name, arg: commands.cmd_group_detail(arg) if arg else "Usage: /group <id>",
    "/pay_fee": lambda chat_id, user_id, username, first_name, arg: commands.cmd_pay_fee(user_id, arg) if arg else "Usage: /pay_fee <fee_id>",
    "/my_fees": lambda chat_id, user_id, username, first_name, arg: commands.cmd_my_fees(user_id),
    "/deposit": lambda chat_id, user_id, username, first_name, arg: commands.cmd_deposit(
        chat_id, user_id, username, first_name),
    "/balance": lambda chat_id, user_id, username, first_name, arg: commands.cmd_balance(user_id),
    "/withdraw": lambda chat_id, user_id, username, first_name, arg: commands.cmd_withdraw(
        chat_id, user_id, username, first_name),
    "/transactions": lambda chat_id, user_id, username, first_name, arg: commands.cmd_transactions(user_id),
    "/contribute": lambda chat_id, user_id, username, first_name, arg: commands.cmd_contribute(
        chat_id, user_id, username, first_name, arg),
}


def _create_group(chat_id, user_id, data):
    """Persist a new group from the wizard's collected data."""
    try:
        group = models.create_group(
            creator_id=user_id,
            name=data["name"],
            contribution_amount=data["contribution_amount"],
            currency=data["currency"],
            frequency=data["frequency"],
            max_participants=data["max_participants"],
            admin_fee_type=data.get("admin_fee_type", "NONE"),
            admin_fee_value=data.get("admin_fee_value", 0),
        )
        return (
            f"Group created!\n\n"
            f"ID: {group['id']}\n"
            f"Name: {group['name']}\n"
            f"Contribution: {group['contribution_amount']} {group['currency']} ({group['frequency']})\n"
            f"Participants: 1/{group['max_participants']}\n"
            f"Admin Fee: {group['admin_fee_type']}:{group['admin_fee_value']}\n\n"
            f"Share the group ID ({group['id']}) with others to have them join with /group {group['id']}"
        )
    except Exception as error:
        return f"Failed to create group: {type(error).__name__}: {error}"


def handle_message(message):
    """Pure dispatch (no Telegram I/O) for easy testing."""
    chat_id = message["chat_id"]
    text = message.get("text", "").strip()
    user_id = message.get("from", {}).get("id", "")
    username = message.get("from", {}).get("username", "")
    first_name = message.get("from", {}).get("first_name", "")

    if not user_id:
        return None

    models.upsert_user(user_id, username=username, first_name=first_name)

    fsm_state = state.get_state(chat_id)

    if fsm_state and text.startswith("/"):
        state.clear_state(chat_id)

    if fsm_state:
        step = fsm_state["step"]
        if step == "AWAITING_NAME":
            return state.handle_name(chat_id, text)
        elif step == "AWAITING_AMOUNT":
            return state.handle_amount(chat_id, text)
        elif step == "AWAITING_FREQUENCY":
            return state.handle_frequency(chat_id, text)
        elif step == "AWAITING_PARTICIPANTS":
            return state.handle_participants(chat_id, text)
        elif step == "AWAITING_FEE_TYPE":
            return state.handle_fee_type(chat_id, text)
        elif step == "AWAITING_FEE_VALUE":
            return state.handle_fee_value(chat_id, text)
        elif step == "CONFIRMING":
            result = state.handle_confirmation(chat_id, text)
            if isinstance(result, tuple) and result[0] == "_create":
                return _create_group(chat_id, user_id, result[1])
            return result
        elif step == "AWAITING_DEPOSIT_AMOUNT":
            return state.handle_deposit_amount(chat_id, text)
        elif step == "AWAITING_DEPOSIT_PROVIDER":
            return state.handle_deposit_provider(chat_id, text)
        elif step == "AWAITING_DEPOSIT_PHONE":
            return state.handle_deposit_phone(chat_id, text)
        elif step == "AWAITING_DEPOSIT_GROUP":
            reply = state.handle_deposit_group(chat_id, text)
            return reply
        elif step == "AWAITING_WITHDRAW_AMOUNT":
            return state.handle_withdraw_amount(chat_id, text)
        elif step == "AWAITING_WITHDRAW_PROVIDER":
            return state.handle_withdraw_provider(chat_id, text)
        elif step == "AWAITING_WITHDRAW_PHONE":
            return state.handle_withdraw_phone(chat_id, text)
        elif step == "CONFIRMING_WITHDRAW":
            return state.handle_withdraw_confirmation(chat_id, text)
        elif step == "AWAITING_CONTRIBUTE_PROVIDER":
            return state.handle_contribute_provider(chat_id, text)
        elif step == "AWAITING_CONTRIBUTE_PHONE":
            return state.handle_contribute_phone(chat_id, text)

    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        handler = _COMMAND_HANDLERS.get(cmd)
        if handler is None:
            return f"Unknown command: {cmd}. Send /help to see available commands."
        return handler(chat_id, user_id, username, first_name, arg)

    return "Send /create_susu to start a group, or /help to see all commands."


def handle_callback(callback):
    chat_id = callback["chat_id"]
    data = callback["data"]
    user_id = callback.get("from", {}).get("id", "")
    username = callback.get("from", {}).get("username", "")
    first_name = callback.get("from", {}).get("first_name", "")
    message_id = callback.get("message_id")

    models.upsert_user(user_id, username=username, first_name=first_name)

    fsm_state = state.get_state(chat_id)

    if fsm_state:
        step = fsm_state["step"]
        if step == "AWAITING_FREQUENCY" and data in ("DAILY", "WEEKLY", "MONTHLY"):
            result = state.handle_frequency(chat_id, data)
            tg.answer_callback_query(callback.get("callback_id", ""), text=data)
            if message_id:
                tg.edit_message_reply_markup(chat_id, message_id, reply_markup={})
            return result, True
        elif step == "AWAITING_FEE_TYPE" and data in ("FLAT", "PERCENTAGE", "NONE"):
            result = state.handle_fee_type(chat_id, data)
            tg.answer_callback_query(callback.get("callback_id", ""), text=data)
            if message_id:
                tg.edit_message_reply_markup(chat_id, message_id, reply_markup={})
            return result, True

    tg.answer_callback_query(callback.get("callback_id", ""))
    if message_id:
        tg.edit_message_reply_markup(chat_id, message_id, reply_markup={})
    return None, False


def _send_reply(chat_id, text):
    try:
        tg.send_message(chat_id, text)
    except Exception as error:
        print(f"[susu] send failed: {type(error).__name__}: {error}")


def run_forever():
    print("[susu] bot starting")
    while True:
        try:
            messages = tg.poll_updates()
        except Exception as error:
            print(f"[susu] poll failed: {type(error).__name__}: {error}")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        for message in messages:
            try:
                if message.get("type") == "callback_query":
                    reply, should_send = handle_callback(message)
                    if should_send and reply:
                        _send_reply(message["chat_id"], reply)
                else:
                    reply = handle_message(message)
                    if reply:
                        _send_reply(message["chat_id"], reply)
            except Exception as error:
                print(f"[susu] error handling message: {type(error).__name__}: {error}")
                try:
                    _send_reply(message["chat_id"], "Sorry, something went wrong.")
                except Exception:
                    pass

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()
