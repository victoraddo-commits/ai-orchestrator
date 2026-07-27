from datetime import datetime

from core.memory import load, save


AUDIT_FILE = "execution_audit.json"


def history():
    entries = load(AUDIT_FILE)
    return entries if isinstance(entries, list) else []


def record(entry):

    entries = history()

    full_entry = {**entry, "timestamp": datetime.now().isoformat()}

    entries.append(full_entry)

    save(AUDIT_FILE, entries)

    return full_entry
