"""Human-takeover registry — persisted so a mission survives process restart.

A takeover is created when the operator must complete a step in the live
browser (captcha, MFA, OTP, identity check). The record is written to disk
*before* the operator is notified, so ``resume_if_completed`` can recover the
paused mission after a crash/restart.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.browser import storage
from core.browser.schema import TakeoverRecord, TakeoverStatus, now_iso

INDEX_FILE = "takeovers.json"


class TakeoverNotFound(KeyError):
    def __init__(self, takeover_id: str):
        super().__init__(f"takeover not found: {takeover_id}")
        self.takeover_id = takeover_id


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class TakeoverStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        storage.ensure_dir(self.root)

    def _path(self) -> Path:
        return self.root / INDEX_FILE

    def _map(self) -> dict:
        data = storage.read_json(self._path(), {"takeovers": {}})
        if isinstance(data, dict) and "takeovers" in data:
            return dict(data["takeovers"])
        return {}

    def create(self, record: TakeoverRecord) -> TakeoverRecord:
        rec = record.model_dump(mode="json")

        def _mutate(_data):
            items = self._map()
            items[rec["takeover_id"]] = rec
            return {"takeovers": items}

        storage.update_json(self._path(), _mutate, default={"takeovers": {}})
        return TakeoverRecord(**rec)

    def get(self, takeover_id: str) -> Optional[TakeoverRecord]:
        rec = self._map().get(takeover_id)
        return TakeoverRecord(**rec) if rec else None

    def active_for_session(self, session_id: str) -> Optional[TakeoverRecord]:
        found = None
        for rec in self._map().values():
            if rec.get("session_id") == session_id and rec.get("status") == TakeoverStatus.pending.value:
                found = rec
        return TakeoverRecord(**found) if found else None

    def list(self, status: Optional[str] = None) -> list[TakeoverRecord]:
        rows = [TakeoverRecord(**r) for r in self._map().values()]
        if status:
            rows = [r for r in rows if r.status.value == status]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)

    def _set_status(self, takeover_id: str, status: TakeoverStatus) -> TakeoverRecord:
        result: dict = {}

        def _mutate(_data):
            items = self._map()
            rec = items.get(takeover_id)
            if rec is None:
                raise TakeoverNotFound(takeover_id)
            rec["status"] = status.value
            rec["resolved_at"] = now_iso()
            items[takeover_id] = rec
            result.update(rec)
            return {"takeovers": items}

        storage.update_json(self._path(), _mutate, default={"takeovers": {}})
        return TakeoverRecord(**result)

    def complete(self, takeover_id: str) -> TakeoverRecord:
        return self._set_status(takeover_id, TakeoverStatus.completed)

    def cancel(self, takeover_id: str) -> TakeoverRecord:
        return self._set_status(takeover_id, TakeoverStatus.cancelled)

    def expire(self, takeover_id: str) -> TakeoverRecord:
        return self._set_status(takeover_id, TakeoverStatus.expired)

    def is_expired(self, record: TakeoverRecord, now: Optional[datetime] = None) -> bool:
        exp = _parse(record.expires_at)
        if exp is None:
            return False
        return (now or datetime.now(timezone.utc)) >= exp

    def expire_stale(self) -> list[str]:
        expired: list[str] = []
        now = datetime.now(timezone.utc)
        for rec in self.list(status=TakeoverStatus.pending.value):
            if self.is_expired(rec, now):
                self.expire(rec.takeover_id)
                expired.append(rec.takeover_id)
        return expired
