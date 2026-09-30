"""Session persistence — save/restore session state so a mission resumes.

State is stored as one atomic ``sessions.json`` index keyed by ``session_id``.
The engine writes Playwright ``storage_state`` to the path recorded on the
session, so a crash/restart can restore cookies without re-authenticating.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.browser import storage
from core.browser.schema import SessionState, now_iso

INDEX_FILE = "sessions.json"


class SessionNotFound(KeyError):
    def __init__(self, session_id: str):
        super().__init__(f"browser session not found: {session_id}")
        self.session_id = session_id


class SessionStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        storage.ensure_dir(self.root)

    def _path(self) -> Path:
        return self.root / INDEX_FILE

    def _map(self) -> dict:
        data = storage.read_json(self._path(), {"sessions": {}})
        if isinstance(data, dict) and "sessions" in data:
            return dict(data["sessions"])
        return {}

    def save(self, state: SessionState) -> SessionState:
        record = state.model_dump(mode="json")
        record["updated_at"] = now_iso()

        def _mutate(_data):
            sessions = self._map()
            sessions[record["session_id"]] = record
            return {"sessions": sessions}

        storage.update_json(self._path(), _mutate, default={"sessions": {}})
        return SessionState(**record)

    def get(self, session_id: str) -> Optional[SessionState]:
        record = self._map().get(session_id)
        return SessionState(**record) if record else None

    def list(self) -> list[SessionState]:
        return [SessionState(**r) for r in self._map().values()]

    def delete(self, session_id: str) -> bool:
        if session_id not in self._map():
            return False

        def _mutate(_data):
            sessions = self._map()
            sessions.pop(session_id, None)
            return {"sessions": sessions}

        storage.update_json(self._path(), _mutate, default={"sessions": {}})
        return True
