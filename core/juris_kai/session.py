"""Session management for Juris Kai Legal Assistant.

Manages user sessions for the legal assistant, maintaining:
  - Educational progress (topics studied, quiz scores, weak areas)
  - Conversation history (last N messages for context)
  - Interaction state (current menu, active flow)
  - Document analysis sessions (session-based, never auto-ingested)

Uses the same atomic write pattern as core.memory (temp file + os.replace)
for durability.  Stores in memory/juris_kai_sessions.json.

Security: NO imports of core.build_manager, core.approval, or
core.deployment_manager.
"""

import json
import os
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Optional

logger = logging.getLogger("juris_kai.session")

# Session storage — in memory/ directory like all other runtime state
STORAGE_PATH = Path(__file__).parent.parent.parent / "memory" / "juris_kai_sessions.json"
MAX_CONVERSATION_HISTORY = 20

# Follow-up context bounds. Small and char-capped so prompt assembly stays
# cheap: bloating the prompt would slow generation, defeating the purpose.
FOLLOWUP_MAX_TURNS = 3
FOLLOWUP_MAX_CHARS = 1500

_write_lock = RLock()

# A question is treated as a follow-up when it explicitly continues the
# previous turn or is very short (anaphoric). Standalone questions keep their
# own cache key and stay FAQ-eligible.
_FOLLOWUP_MARKERS = (
    "and ", "and the", "and what", "what about", "how about", "what if",
    "then ", "also ", "so ", "why ", "why?", "really", "more", "elaborate",
    "continue", "explain more", "go on", "tell me more",
)
_FOLLOWUP_RE = re.compile("|".join(re.escape(m) for m in _FOLLOWUP_MARKERS),
                          re.IGNORECASE)


def looks_like_followup(text: str) -> bool:
    """Heuristic: is this question continuing the previous turn?"""
    t = (text or "").strip().lower()
    if not t:
        return False
    if len(t.split()) <= 2:
        return True
    return bool(_FOLLOWUP_RE.match(t))


def _load() -> dict:
    """Load all sessions from disk. Returns empty dict on any error."""
    try:
        if STORAGE_PATH.exists():
            return json.loads(STORAGE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save(data: dict) -> None:
    """Atomic write — temp file + os.replace under a lock."""
    with _write_lock:
        try:
            STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STORAGE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.chmod(0o600)
            tmp.replace(STORAGE_PATH)
        except OSError as e:
            logger.error(f"Failed to save sessions: {e}")


def get_user_session(chat_id: str | int) -> dict:
    """Get or create a user session for legal learning.

    Returns a dict with keys:
      - topics_studied: list[str] — topics the user has studied
      - quiz_scores: dict — topic -> list of scores
      - weak_areas: list[str] — topics with low scores
      - conversation_history: list[dict] — last N messages {role, content, timestamp}
      - current_menu: str | None — current active menu
      - preferences: dict — user preferences
      - created_at: str — ISO timestamp
      - updated_at: str — ISO timestamp
    """
    with _write_lock:
        sessions = _load()
        key = str(chat_id)

        if key not in sessions:
            now = datetime.now(timezone.utc).isoformat()
            sessions[key] = {
                "topics_studied": [],
                "quiz_scores": {},
                "weak_areas": [],
                "conversation_history": [],
                "current_menu": None,
                "document_sessions": {},
                "preferences": {"language": "en", "learning_level": "beginner"},
                "created_at": now,
                "updated_at": now,
            }
            _save(sessions)

        return sessions[key]


def update_session(chat_id: str | int, **kwargs) -> dict:
    """Update session fields and persist. Returns the updated session."""
    with _write_lock:
        sessions = _load()
        key = str(chat_id)

        if key not in sessions:
            # Recursive call — safe because the lock is re-entrant (threading.RLock)
            session = get_user_session(chat_id)
            sessions = _load()

        session = sessions[key]
        session.update(kwargs)
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(sessions)
        return session


def record_topic_studied(chat_id: str | int, topic: str) -> None:
    """Record that a user studied a topic."""
    session = get_user_session(chat_id)
    if topic not in session["topics_studied"]:
        session["topics_studied"].append(topic)
        update_session(chat_id, topics_studied=session["topics_studied"])


def record_quiz_score(chat_id: str | int, topic: str, score: float) -> None:
    """Record a quiz score for a topic."""
    session = get_user_session(chat_id)
    scores = session.get("quiz_scores", {})
    if topic not in scores:
        scores[topic] = []
    scores[topic].append(score)
    # Track weak areas
    if score < 60 and topic not in session.get("weak_areas", []):
        weak = session.get("weak_areas", [])
        weak.append(topic)
        update_session(chat_id, quiz_scores=scores, weak_areas=weak)
    else:
        update_session(chat_id, quiz_scores=scores)


def add_conversation_message(chat_id: str | int, role: str, content: str) -> None:
    """Add a message to the conversation history (rolling buffer)."""
    session = get_user_session(chat_id)
    history = session.get("conversation_history", [])
    history.append({
        "role": role,
        "content": content[:500],  # truncate for storage
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    # Keep only the last N messages
    if len(history) > MAX_CONVERSATION_HISTORY:
        history = history[-MAX_CONVERSATION_HISTORY:]
    update_session(chat_id, conversation_history=history)


def get_conversation_context(chat_id: str | int, n: int = 5) -> str:
    """Get the last N conversation messages as context for AI prompting."""
    session = get_user_session(chat_id)
    history = session.get("conversation_history", [])
    recent = history[-n:] if len(history) > n else history
    if not recent:
        return ""
    lines = []
    for msg in recent:
        role_label = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"[{role_label}]: {msg['content'][:200]}")
    return "Previous conversation:\n" + "\n".join(lines)


def record_conversation_turn(chat_id: str | int, question: str,
                             answer: str) -> None:
    """Append a user/assistant pair to the rolling history in one write."""
    session = get_user_session(chat_id)
    history = session.get("conversation_history", [])
    now = datetime.now(timezone.utc).isoformat()
    for role, content in (("user", question), ("assistant", answer)):
        history.append({
            "role": role,
            "content": (content or "")[:500],  # truncate for storage
            "timestamp": now,
        })
    if len(history) > MAX_CONVERSATION_HISTORY:
        history = history[-MAX_CONVERSATION_HISTORY:]
    update_session(chat_id, conversation_history=history)


def get_recent_turns(chat_id: str | int, turns: int = FOLLOWUP_MAX_TURNS,
                     max_chars: int = FOLLOWUP_MAX_CHARS) -> str:
    """Render the last ``turns`` user/assistant pairs, hard-capped by chars.

    Newest messages win when the cap is reached, so the most relevant
    (immediately prior) turn is always present.
    """
    session = get_user_session(chat_id)
    history = session.get("conversation_history", [])
    if not history:
        return ""
    recent = history[-(int(turns) * 2):]
    selected: list[str] = []
    used = 0
    for msg in reversed(recent):
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        role_label = "User" if msg.get("role") == "user" else "Assistant"
        line = f"{role_label}: {content}"
        if selected and used + len(line) > max_chars:
            break
        selected.append(line)
        used += len(line)
    if not selected:
        return ""
    selected.reverse()
    return ("Recent conversation context (for follow-up questions):\n"
            + "\n".join(selected))


def get_followup_context(chat_id: str | int, question: str,
                         turns: int = FOLLOWUP_MAX_TURNS,
                         max_chars: int = FOLLOWUP_MAX_CHARS) -> str:
    """Context string for ``question`` — empty unless it looks like a follow-up."""
    if not looks_like_followup(question):
        return ""
    return get_recent_turns(chat_id, turns=turns, max_chars=max_chars)


def set_current_menu(chat_id: str | int, menu_name: str | None) -> None:
    """Track which menu the user is currently on."""
    update_session(chat_id, current_menu=menu_name)


def get_learning_stats(chat_id: str | int) -> dict:
    """Get aggregated learning statistics for a user."""
    session = get_user_session(chat_id)
    scores = session.get("quiz_scores", {})
    return {
        "topics_studied": len(session.get("topics_studied", [])),
        "total_quizzes": sum(len(v) for v in scores.values()),
        "average_score": (
            sum(sum(v) / len(v) for v in scores.values()) / len(scores)
            if scores else 0
        ),
        "weak_areas": session.get("weak_areas", []),
        "last_active": session.get("updated_at"),
    }


# Security: same pattern as law_tutor — no imports of operational modules
# Session persistence uses atomic writes to memory/ directory
