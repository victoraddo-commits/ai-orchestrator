"""Session Center (roadmap 21Q).

Attach/detach/reconnect/inspect/interrupt/resume/approve teammate sessions.
Hard invariant: detaching a browser/phone session MUST NOT terminate the
mission — detach only drops the view; the mission keeps running.
"""
from __future__ import annotations

import time


class SessionCenter:
    def __init__(self):
        self._sessions: dict = {}

    def attach(self, session_id: str, mission_id: str, meta: dict = None) -> dict:
        session = {"session_id": session_id, "mission_id": mission_id,
                   "attached_at": time.time(), "state": "attached",
                   "meta": dict(meta or {}), "interrupted": False}
        self._sessions[session_id] = session
        return session

    def detach(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session["state"] = "detached"      # view dropped; mission untouched
        return True

    def reconnect(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session["state"] = "attached"
        return True

    def inspect(self, session_id: str) -> dict:
        return dict(self._sessions.get(session_id, {}))

    def interrupt(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session["interrupted"] = True
        return True

    def resume(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session["interrupted"] = False
        return True

    def approve(self, session_id: str, decision: str = "approved") -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            return {}
        session["decision"] = decision
        return {"session_id": session_id, "decision": decision,
                "mission_id": session["mission_id"]}

    def list(self) -> list:
        return [dict(s) for s in self._sessions.values()]

    def mission_of(self, session_id: str) -> str:
        return self._sessions.get(session_id, {}).get("mission_id", "")
