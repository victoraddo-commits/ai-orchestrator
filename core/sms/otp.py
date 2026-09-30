"""core.sms OTP hand-off — in-memory only.

A verification code is the one piece of SMS data that must exist transiently:
it is detected inside an untrusted message, correlated to the mission/account
that is awaiting verification, and handed to the onboarding flow **in memory**
with a short TTL. The code is never written to a store, an event, an audit
entry, a log, Telegram, or the Account Registry.

The store keeps each code as a :class:`bytearray` and overwrites it with zero
bytes on consume/expiry, so a consumed code cannot be recovered from the
process's retained object graph. (Python ``str`` copies are transient; callers
receive a fresh string and must drop it after use.)
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Optional

DEFAULT_TTL_SECONDS = 180.0


class OtpHandoffStore:
    def __init__(self, ttl: float = DEFAULT_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self._ttl = float(ttl)
        self._clock = clock
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _zeroize(entry: dict) -> None:
        buf: bytearray = entry["code"]
        buf[:] = b"\x00" * len(buf)

    def _purge_locked(self) -> None:
        now = self._clock()
        for token in [t for t, e in self._items.items()
                      if e["expires_at"] <= now or e["used"]]:
            entry = self._items.pop(token)
            self._zeroize(entry)

    @staticmethod
    def _matches(entry: dict, mission_id: Optional[str], account_id: Optional[str]) -> bool:
        if mission_id is not None and entry["mission_id"] != mission_id:
            return False
        if account_id is not None and entry["account_id"] != account_id:
            return False
        return mission_id is not None or account_id is not None

    # -- public API --------------------------------------------------------

    def stash(self, code: str, *, mission_id: Optional[str] = None,
              account_id: Optional[str] = None, ttl: Optional[float] = None) -> str:
        """Keep *code* transiently. Returns an opaque token (never the code)."""
        token = f"otp-{uuid.uuid4().hex}"
        entry = {
            "code": bytearray(str(code).encode("utf-8")),
            "mission_id": mission_id,
            "account_id": account_id,
            "expires_at": self._clock() + (self._ttl if ttl is None else float(ttl)),
            "used": False,
        }
        with self._lock:
            self._purge_locked()
            self._items[token] = entry
        return token

    def pending(self, *, mission_id: Optional[str] = None,
                account_id: Optional[str] = None) -> Optional[str]:
        with self._lock:
            self._purge_locked()
            for token, entry in self._items.items():
                if self._matches(entry, mission_id, account_id):
                    return token
        return None

    def consume(self, token: Optional[str]) -> Optional[str]:
        """Return the code once and zeroize it; subsequent calls return None."""
        if not token:
            return None
        with self._lock:
            self._purge_locked()
            entry = self._items.pop(token, None)
            if entry is None or entry["used"]:
                return None
            code = bytes(entry["code"]).decode("utf-8")
            entry["used"] = True
            self._zeroize(entry)
            return code

    def consume_for(self, *, mission_id: Optional[str] = None,
                    account_id: Optional[str] = None) -> Optional[str]:
        return self.consume(self.pending(mission_id=mission_id, account_id=account_id))

    def purge_expired(self) -> None:
        with self._lock:
            self._purge_locked()

    # -- test/inspection helper (never returns a live code after consume) --

    def _debug_code_bytes(self, token: str) -> Optional[bytes]:
        entry = self._items.get(token)
        return bytes(entry["code"]) if entry else None


#: Process-wide singleton. Manager code must reference ``otp.handoffs`` (module
#: attribute) so tests can swap it for an isolated instance.
handoffs = OtpHandoffStore()


def stash_otp(code: str, *, mission_id: Optional[str] = None,
              account_id: Optional[str] = None, ttl: Optional[float] = None) -> str:
    return handoffs.stash(code, mission_id=mission_id, account_id=account_id, ttl=ttl)


def consume_otp(*, mission_id: Optional[str] = None,
                account_id: Optional[str] = None) -> Optional[str]:
    return handoffs.consume_for(mission_id=mission_id, account_id=account_id)


def pending_otp(*, mission_id: Optional[str] = None,
                account_id: Optional[str] = None) -> Optional[str]:
    return handoffs.pending(mission_id=mission_id, account_id=account_id)


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "OtpHandoffStore",
    "handoffs",
    "stash_otp",
    "consume_otp",
    "pending_otp",
]
