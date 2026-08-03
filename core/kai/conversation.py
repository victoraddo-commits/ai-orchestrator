"""Phase 17V: Kai conversation memory — session envelopes, operator long-term
store, and guarded compression.

Distinct from 13F (build-learning memory) and 15F (multi-user chat history).
Single-operator scoped, builds on 13X (POST /kai/chat), no auth dependency.
Adds 15F functionality: conversation persistence, streaming, and search.
"""

import json
import os
import re
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from typing import List, Dict, Optional, Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAT_HISTORY_FILE = "kai_chat_history.json"
OPERATOR_LONG_TERM_FILE = "operator_long_term.json"

# ---------------------------------------------------------------------------
# Conversation Persistence Implementation
# ---------------------------------------------------------------------------

# Conversation storage file
CONVERSATIONS_FILE = "kai_conversations.json"
MESSAGES_FILE = "kai_messages.json"

def _load_conversations() -> List[Dict]:
    """Load all saved conversations."""
    try:
        from core.memory import load
        data = load(CONVERSATIONS_FILE)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and data.get("schema_version") == 1:
            return data.get("conversations", [])
        return []
    except Exception:
        return []

def _save_conversations(conversations: List[Dict]) -> None:
    """Save conversations to memory."""
    from core.memory import save
    save(CONVERSATIONS_FILE, {
        "schema_version": 1,
        "conversations": conversations
    })

def _load_messages() -> List[Dict]:
    """Load all saved messages."""
    try:
        from core.memory import load
        data = load(MESSAGES_FILE)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and data.get("schema_version") == 1:
            return data.get("messages", [])
        return []
    except Exception:
        return []

def _save_messages(messages: List[Dict]) -> None:
    """Save messages to memory."""
    from core.memory import save
    save(MESSAGES_FILE, {
        "schema_version": 1,
        "messages": messages
    })

def create_conversation(user_id: str, project_id: Optional[str] = None) -> str:
    """Create a new conversation and return its ID."""
    conversation_id = str(uuid.uuid4())
    
    # Generate title from first message content
    title = "New Conversation"
    
    conversation = {
        "id": conversation_id,
        "user_id": user_id,
        "project_id": project_id,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    conversations = _load_conversations()
    conversations.append(conversation)
    _save_conversations(conversations)
    
    return conversation_id

def get_conversations(user_id: str, project_id: Optional[str] = None) -> List[Dict]:
    """Get all conversations for a user, optionally filtered by project."""
    conversations = _load_conversations()
    return [c for c in conversations if c.get("user_id") == user_id and 
           (project_id is None or c.get("project_id") == project_id)]

def get_conversation(conversation_id: str) -> Optional[Dict]:
    """Get a specific conversation by ID."""
    conversations = _load_conversations()
    for conv in conversations:
        if conv.get("id") == conversation_id:
            return conv
    return None

def update_conversation_title(conversation_id: str, title: str) -> bool:
    """Update the title of a conversation."""
    conversations = _load_conversations()
    for conv in conversations:
        if conv.get("id") == conversation_id:
            conv["title"] = title
            conv["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_conversations(conversations)
            return True
    return False

def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation and all its messages."""
    conversations = _load_conversations()
    conversations = [c for c in conversations if c.get("id") != conversation_id]
    _save_conversations(conversations)
    
    # Also delete associated messages
    messages = _load_messages()
    messages = [m for m in messages if m.get("conversation_id") != conversation_id]
    _save_messages(messages)
    
    return True

def create_message(conversation_id: str, role: str, content: str) -> str:
    """Create a new message in a conversation and return its ID."""
    message_id = str(uuid.uuid4())
    
    message = {
        "id": message_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    messages = _load_messages()
    messages.append(message)
    _save_messages(messages)
    
    return message_id

def get_messages(conversation_id: str) -> List[Dict]:
    """Get all messages for a conversation."""
    messages = _load_messages()
    return [m for m in messages if m.get("conversation_id") == conversation_id]

def search_conversations(user_id: str, query: str = "") -> List[Dict]:
    """Search conversations for a user by title or content."""
    conversations = get_conversations(user_id)
    
    if not query:
        return conversations
    
    # Simple text search in titles
    results = []
    for conv in conversations:
        title = conv.get("title", "")
        if query.lower() in title.lower():
            results.append(conv)
    
    return results

EMPTY_ENVELOPE = {
    "schema_version": 2,
    "session": {
        "session_id": "",
        "active_goal": None,
        "created": "",
    },
    "recent_messages": [],
    "ephemeral_context": {
        "last_referenced_document": None,
        "last_referenced_build": None,
        "last_referenced_phase": None,
    },
    "compressed": {
        "older_summary": None,
        "key_entities": [],
        "compressed_at": None,
    },
}


def _read_chat_history(directory=None):
    """Read the chat history file, transparently upgrading old-format files."""
    from core.memory import load

    data = load(CHAT_HISTORY_FILE, directory=directory)

    # Already in envelope format
    if isinstance(data, dict) and data.get("schema_version") == 2:
        return data

    # Old flat-array format (schema_version 1 or bare list)
    if isinstance(data, (list, dict)):
        messages = data if isinstance(data, list) else data.get("records", [])
        envelope = dict(EMPTY_ENVELOPE)
        envelope["session"]["session_id"] = _new_session_id()
        envelope["session"]["created"] = _now()
        envelope["recent_messages"] = [dict(m) for m in messages] if messages else []
        # If there's an active goal in the last user message, capture it
        for msg in reversed(envelope["recent_messages"]):
            if msg.get("role") == "user":
                goal = _extract_goal(msg.get("content", ""))
                if goal:
                    envelope["session"]["active_goal"] = goal
                break
        return envelope

    return dict(EMPTY_ENVELOPE)


def _write_chat_history(envelope, directory=None):
    """Persist the session envelope atomically."""
    from core.memory import save

    save(CHAT_HISTORY_FILE, envelope, directory=directory)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _new_session_id():
    import uuid
    return uuid.uuid4().hex[:12]


def _extract_goal(text):
    """Try to extract the current goal from a user message."""
    # Simple heuristic: first sentence that looks like a task
    for sentence in re.split(r'[.!?]\s+', text[:500]):
        sentence = sentence.strip()
        if len(sentence) > 20 and any(w in sentence.lower() for w in
                                       ('build', 'implement', 'create', 'fix', 'add',
                                        'continue', 'start', 'work on', 'design', 'deploy')):
            return sentence[:200]
    return None


def add_message(role, content, directory=None):
    """Append a message to the session.  Triggers compression when the
    threshold is exceeded."""
    envelope = _read_chat_history(directory)

    if not envelope["session"]["session_id"]:
        envelope["session"]["session_id"] = _new_session_id()
        envelope["session"]["created"] = _now()

    if role == "user":
        goal = _extract_goal(content)
        if goal:
            envelope["session"]["active_goal"] = goal

    envelope["recent_messages"].append({
        "role": role,
        "content": content,
        "timestamp": _now(),
    })

    # Check compression threshold
    if len(envelope["recent_messages"]) > COMPRESSION_THRESHOLD:
        envelope = _compress(envelope)

    _write_chat_history(envelope, directory=directory)
    return envelope


def get_session(directory=None):
    """Return the current session envelope (read-only snapshot)."""
    return _read_chat_history(directory)


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

# Patterns that must survive compression verbatim
_CITATION_PATTERNS = [
    # Legal citations: Act 123, LI 456, Constitution Article 12(3)
    re.compile(r'\b(?:Act|Acts|LI|Legislative\s+Instrument|Constitution|Constitutional\s+Instrument|C\.I\.)\s+\d+[A-Z]?', re.IGNORECASE),
    # Case names: Smith v Jones [2020] SCGLR 123
    re.compile(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+v\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*', re.IGNORECASE),
    # Court references
    re.compile(r'\b(?:Supreme\s+Court|Court\s+of\s+Appeal|High\s+Court)\b', re.IGNORECASE),
    # Article/section references
    re.compile(r'\b(?:Article|Section|Clause)\s+\d+(?:\(\d+\))?[A-Za-z]?', re.IGNORECASE),
    # Year-citation patterns: [2020] SCGLR 123, (2019) JELR 45
    re.compile(r'\[?\d{4}\]?\s+[A-Z]+\s+\d+', re.IGNORECASE),
]

_DIRECTIVE_PATTERNS = [
    re.compile(r'\b(?:always|never|do\s+not|must\s+not|must|shall\s+not|shall)\b[^.!?]*(?:[.!?]|$)', re.IGNORECASE),
    re.compile(r'operator\s+directive[^.!?]*(?:[.!?]|$)', re.IGNORECASE),
]


def _extract_key_entities(messages):
    """Extract citations, directives, and corrections that must survive
    compression.  Returns a list of extracted strings."""
    entities = []

    for msg in messages:
        content = msg.get("content", "")

        # Legal citations
        for pattern in _CITATION_PATTERNS:
            for match in pattern.finditer(content):
                entities.append({
                    "type": "citation",
                    "value": match.group(),
                    "source_message_role": msg.get("role"),
                })

        # Operator directives
        for pattern in _DIRECTIVE_PATTERNS:
            for match in pattern.finditer(content):
                text = match.group().strip()
                if len(text) > 10:  # skip false positives
                    entities.append({
                        "type": "directive",
                        "value": text[:300],
                        "source_message_role": msg.get("role"),
                    })

        # Explicit corrections
        if msg.get("role") == "user":
            correction_markers = [
                r"(?:no[,;]\s+)?(?:(?:that'?s|that is|you'?re|you are)\s+(?:wrong|incorrect|not\s+(?:right|correct)))",
                r"(?:I\s+(?:meant|said|called|referred\s+to))\s+([^.!?]+)",
                r"correct(?:ion)?:\s*([^.!?]+)",
            ]
            for marker in correction_markers:
                for match in re.finditer(marker, content, re.IGNORECASE):
                    entities.append({
                        "type": "correction",
                        "value": match.group().strip()[:300],
                        "source_message_role": msg.get("role"),
                    })

    return entities


def _compress(envelope):
    """Compress older messages into a structured summary while preserving
    key entities and the most recent turns verbatim."""
    messages = envelope["recent_messages"]
    split_idx = max(0, len(messages) - KEEP_RECENT_TURNS)
    older = messages[:split_idx]
    recent = messages[split_idx:]

    if not older:
        return envelope

    # Extract key entities from the older messages
    entities = _extract_key_entities(older)
    for entity in entities:
        if entity not in envelope["compressed"].get("key_entities", []):
            envelope["compressed"].setdefault("key_entities", []).append(entity)

    # Build a structured summary of the older turns
    older_summary_parts = []
    for msg in older[-15:]:  # summarize the last 15 of the older block
        role = "Operator" if msg.get("role") == "user" else "Kai"
        content = msg.get("content", "")[:200]
        older_summary_parts.append(f"[{role}]: {content}")

    envelope["compressed"]["older_summary"] = "\n".join(older_summary_parts)
    envelope["compressed"]["compressed_at"] = _now()
    envelope["recent_messages"] = recent

    return envelope


# ---------------------------------------------------------------------------
# Long-term operator memory
# ---------------------------------------------------------------------------

EMPTY_LONG_TERM = {
    "schema_version": 1,
    "operator": "single-operator",
    "facts": [],       # [{"key": "...", "value": "...", "stored_at": "..."}]
    "directives": [],  # [{"directive": "...", "stored_at": "..."}]
    "preferences": {}, # arbitrary key-value map
}


def _read_long_term(directory=None):
    """Read the operator long-term store."""
    try:
        from core.memory import load
        data = load(OPERATOR_LONG_TERM_FILE, directory=directory)
        if isinstance(data, dict) and data.get("schema_version") == 1:
            return data
    except Exception:
        pass
    return dict(EMPTY_LONG_TERM)


def _write_long_term(store, directory=None):
    """Persist the operator long-term store atomically."""
    from core.memory import save
    save(OPERATOR_LONG_TERM_FILE, store, directory=directory)


def remember_fact(key, value, directory=None):
    """Store a fact in long-term memory. Only called when the operator
    explicitly says 'remember that...' — nothing auto-writes to this."""
    store = _read_long_term(directory)

    # Remove existing entry with the same key (upsert)
    store["facts"] = [f for f in store["facts"] if f.get("key") != key]
    store["facts"].append({
        "key": key,
        "value": value,
        "stored_at": _now(),
    })
    _write_long_term(store, directory=directory)


def remember_directive(directive, directory=None):
    """Store an operator directive ('always', 'never', 'do not', etc.)."""
    store = _read_long_term(directory)
    store["directives"] = [d for d in store["directives"]
                           if d.get("directive") != directive]
    store["directives"].append({
        "directive": directive,
        "stored_at": _now(),
    })
    _write_long_term(store, directory=directory)


def get_long_term_context(directory=None):
    """Return the full operator long-term store as a string suitable for
    injection into a system prompt."""
    store = _read_long_term(directory)

    parts = []
    if store.get("facts"):
        parts.append("Operator facts:")
        for f in store["facts"]:
            parts.append(f"  - {f['key']}: {f['value']}")

    if store.get("directives"):
        parts.append("\nOperator directives (must follow):")
        for d in store["directives"]:
            parts.append(f"  - {d['directive']}")

    if store.get("preferences"):
        parts.append("\nOperator preferences:")
        for k, v in store["preferences"].items():
            parts.append(f"  - {k}: {v}")

    return "\n".join(parts) if parts else ""


def set_preference(key, value, directory=None):
    """Set an operator preference."""
    store = _read_long_term(directory)
    store.setdefault("preferences", {})[key] = value
    _write_long_term(store, directory=directory)


# ---------------------------------------------------------------------------
# Chat prompt builder — what core.ai.ai_router.chat() calls
# ---------------------------------------------------------------------------

def build_chat_prompt(messages, signals, directory=None):
    """Build the full chat prompt with session context, long-term memory,
    compressed history, and recent messages."""

    envelope = _read_chat_history(directory)
    long_term = get_long_term_context(directory)
    compressed = envelope.get("compressed", {})
    recent = envelope["recent_messages"]

    # Long-term context
    parts = []
    if long_term:
        parts.append(f"## Operator's long-term context\n{long_term}\n")

    # Compressed history
    older_summary = compressed.get("older_summary")
    key_entities = compressed.get("key_entities", [])
    if older_summary:
        parts.append("## Older conversation (summarized)\n")
        parts.append(older_summary)
    if key_entities:
        parts.append("\n## Preserved citations and directives\n")
        for entity in key_entities:
            parts.append(f"  [{entity['type']}] {entity['value']}")

    # Session context
    session = envelope.get("session", {})
    if session.get("active_goal"):
        parts.append(f"\n## Current objective\n  {session['active_goal']}")

    # Recent messages (last N verbatim)
    if recent:
        parts.append("\n## Recent conversation\n")
        for msg in recent[-(COMPRESSION_THRESHOLD):]:
            role = "Operator" if msg.get("role") == "user" else "Kai"
            parts.append(f"{role}: {msg.get('content', '')}")

    # System state
    import json as _json
    prompt = (
        "You are Kai, the operator's assistant. Answer questions truthfully "
        "using only the provided state below. Do not perform any actions, "
        "do not make any changes to the system, and do not suggest any "
        "actions the system could take on its own. You are a conversational "
        "interface for a human operator — answer the question directly and "
        "concisely.\n\n"
        f"{chr(10).join(parts)}\n\n"
        f"Current system state:\n{_json.dumps(signals, indent=2, default=str)}\n\n"
        "Now respond to the operator's most recent message."
    )

    return prompt
