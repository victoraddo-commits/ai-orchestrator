"""Session management for Juris Kai Legal Assistant

Manages user sessions for the legal assistant, maintaining educational progress
and interaction state. Follows the same security constraints as law_tutor.
"""

import json
import os
from pathlib import Path

# Session storage location (same pattern as law_tutor)
SESSIONS_FILE = "juris_kai_sessions.json"

def get_user_session(chat_id):
    """Get or create a user session for legal learning."""
    
    # Load existing sessions (same pattern as law_tutor)
    try:
        with open(SESSIONS_FILE, 'r') as f:
            sessions = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        sessions = {}
    
    # Initialize session if not exists
    if str(chat_id) not in sessions:
        sessions[str(chat_id)] = {
            "topics_studied": [],
            "progress": {},
            "preferences": {}
        }
    
    # Save session (same safe pattern as law_tutor)
    try:
        with open(SESSIONS_FILE, 'w') as f:
            json.dump(sessions, f)
    except Exception:
        # Silently fail - session state is not critical for security
        pass
    
    return sessions[str(chat_id)]

# Same security pattern as law_tutor - no imports of operational modules
# Session persistence is purely local and educational