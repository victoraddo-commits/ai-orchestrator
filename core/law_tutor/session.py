"""Per-student conversation history and progress tracking for the Law Tutor
bot. Single JSON file (memory/law_tutor_sessions.json), keyed by chat_id --
there is exactly one authorized student per deployment of this bot (see
bot.py), but keying by chat_id rather than hardcoding keeps this file
reusable if that ever changes.
"""

from datetime import datetime

from core.memory import load, save

SESSIONS_FILE = "law_tutor_sessions.json"

MAX_HISTORY_MESSAGES = 20


def _load_all():
    data = load(SESSIONS_FILE)
    return data if isinstance(data, dict) else {}


def _save_all(data):
    save(SESSIONS_FILE, data)


def _default_session():
    return {"history": [], "topics_studied": [], "needs_practice": []}


def get_session(chat_id):
    data = _load_all()
    return data.get(chat_id, _default_session())


def append_exchange(chat_id, command, student_text, reply_text, topic=None):
    data = _load_all()
    session = data.setdefault(chat_id, _default_session())

    session["history"].append({
        "at": datetime.now().isoformat(),
        "command": command,
        "student": student_text,
        "reply": reply_text,
    })
    session["history"] = session["history"][-MAX_HISTORY_MESSAGES:]

    if topic and topic not in session["topics_studied"]:
        session["topics_studied"].append(topic)

    _save_all(data)
    return session


def mark_needs_practice(chat_id, topic):
    data = _load_all()
    session = data.setdefault(chat_id, _default_session())
    if topic not in session["needs_practice"]:
        session["needs_practice"].append(topic)
    _save_all(data)
    return session


def recent_context_text(chat_id, limit=6):
    """A short plain-text summary of recent exchanges, for grounding the
    next prompt -- not the full history (keeps prompts small)."""
    session = get_session(chat_id)
    recent = session["history"][-limit:]
    if not recent:
        return "(No prior conversation recorded yet.)"

    lines = []
    for entry in recent:
        lines.append(f"Student ({entry['command']}): {entry['student'][:200]}")
        lines.append(f"Kai: {entry['reply'][:300]}")
    return "\n".join(lines)


def progress_summary_text(chat_id):
    session = get_session(chat_id)
    topics = session["topics_studied"]
    weak = session["needs_practice"]

    lines = []
    lines.append(f"Topics studied ({len(topics)}): " + (", ".join(topics) if topics else "none yet"))
    lines.append(f"Flagged for more practice ({len(weak)}): " + (", ".join(weak) if weak else "none"))
    return "\n".join(lines)
