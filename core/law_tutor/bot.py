"""Law Tutor bot: polling loop, authorization, and command dispatch.

Security boundary: this module and everything it imports (telegram_client,
prompt, session, commands) must never import core.build_manager,
core.approval, core.deployment_manager, or anything else that grants an
operational capability. This bot is education-only, for one specific,
non-operational user (a family member, not an operator of this system) --
enforced by keeping the import graph disjoint, the same way core/kai/'s
chat path is kept structurally separate from direct approve_* calls.

Authorization: LAW_TUTOR_CHAT_ID in the environment is the ONLY chat this
bot will tutor. Unset, or a message from any other chat, gets a polite
decline and is logged (stdout -> journalctl) so the operator can see the
sender's chat_id and configure it -- there is no self-service pairing flow,
since this is meant for exactly one trusted family member added by the
operator, not an open bot.
"""

import os
import time

from dotenv import load_dotenv

load_dotenv()

from core.law_tutor import telegram_client as tg
from core.law_tutor import commands

POLL_INTERVAL_SECONDS = 3

HELP_TEXT = """Hi! I'm Kai, in Legal Intelligence mode. Commands:

/learn <topic> - explain a legal concept, Socratic-style
/case <case name> - case brief (facts, issue, rule, analysis, decision, precedent)
/research <question> - investigate a legal question in depth
/argument <issue> - full argument: issue, law, both sides, evaluation, conclusion
/debate <topic> - argue both sides, more conversational than /argument
/quiz [topic] - test your understanding
/flashcards <topic> - generate flashcards
/exam <topic> - a mock exam question
/irac <fact pattern> - structure an IRAC answer
/studyplan - build/update a revision plan
/needspractice <topic> - flag a topic for extra revision
/progress - see what you've studied and what's flagged
/help - show this again

You can also just message me normally and I'll do my best to help.

Note: I don't yet ingest uploaded textbooks/PDFs into a persistent knowledge base -- ask your admin if you want that built."""

_COMMAND_HANDLERS = {
    "/learn": lambda chat_id, arg: commands.cmd_learn(chat_id, arg),
    "/case": lambda chat_id, arg: commands.cmd_case(chat_id, arg),
    "/research": lambda chat_id, arg: commands.cmd_research(chat_id, arg),
    "/argument": lambda chat_id, arg: commands.cmd_argument(chat_id, arg),
    "/quiz": lambda chat_id, arg: commands.cmd_quiz(chat_id, arg),
    "/flashcards": lambda chat_id, arg: commands.cmd_flashcards(chat_id, arg),
    "/exam": lambda chat_id, arg: commands.cmd_exam(chat_id, arg),
    "/irac": lambda chat_id, arg: commands.cmd_irac(chat_id, arg),
    "/studyplan": lambda chat_id, arg: commands.cmd_studyplan(chat_id, arg),
    "/debate": lambda chat_id, arg: commands.cmd_debate(chat_id, arg),
    "/needspractice": lambda chat_id, arg: commands.cmd_needs_practice(chat_id, arg),
    "/progress": lambda chat_id, arg: commands.cmd_progress(chat_id),
    "/start": lambda chat_id, arg: HELP_TEXT,
    "/help": lambda chat_id, arg: HELP_TEXT,
}


def _allowed_chat_id():
    return os.environ.get("LAW_TUTOR_CHAT_ID", "").strip()


def handle_message(message):
    """Pure-ish dispatch (no Telegram I/O) so this is easy to test directly."""
    chat_id = message["chat_id"]
    allowed = _allowed_chat_id()

    if not allowed or chat_id != allowed:
        sender = message.get("from", {})
        print(
            f"[law_tutor] message from unauthorized chat_id={chat_id} "
            f"username={sender.get('username')} first_name={sender.get('first_name')} "
            "-- set LAW_TUTOR_CHAT_ID to this value to authorize."
        )
        return "This bot is private and hasn't been set up for you yet."

    text = message["text"].strip()
    if not text:
        return None

    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        handler = _COMMAND_HANDLERS.get(cmd)
        if handler is None:
            return f"Unknown command {cmd}. Send /help to see what's available."
        return handler(chat_id, arg)

    # Plain message, no command -- treat as a general "teach me about this".
    return commands.cmd_learn(chat_id, text)


def run_forever():
    print("[law_tutor] bot starting")
    while True:
        try:
            messages = tg.poll_updates()
        except Exception as error:
            print(f"[law_tutor] poll failed: {type(error).__name__}: {error}")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        for message in messages:
            try:
                reply = handle_message(message)
                if reply:
                    tg.send_message(message["chat_id"], reply)
            except Exception as error:
                print(f"[law_tutor] error handling message: {type(error).__name__}: {error}")
                try:
                    tg.send_message(message["chat_id"], "Sorry, something went wrong on my end.")
                except Exception:
                    pass

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()
