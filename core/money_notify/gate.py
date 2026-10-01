"""core.money_notify.gate — §45 delivery gate: prefs mode, quiet hours,
rate limit, dedupe window. Pure decision logic with an injectable clock so
tests never sleep. Security/anomaly types bypass every silencer (always on).
"""
from __future__ import annotations

from . import prefs as _prefs


def _default_clock() -> float:
    import time
    return time.time()


class Gate:
    def __init__(self, prefs: dict | None = None, clock=None):
        self._prefs = prefs
        self._clock_fn = clock or _default_clock

    def decide(self, type_name: str, dedupe_key: str) -> dict:
        """Return {"deliver": bool, "hold_digest": bool, "reason": str}."""
        st = _prefs.load_state()
        now = self._clock_fn()
        mode = _prefs.mode_for(type_name, self._prefs)
        if mode == "off" and type_name not in _prefs.ALWAYS_ON:
            return {"deliver": False, "hold_digest": False, "reason": "type off"}

        window = int(self._prefs.get("dedupe_window_sec", 600) or 0)
        seen = (st.get("dedupe") or {}).get(dedupe_key)
        if seen is not None and now - float(seen) < window:
            return {"deliver": False, "hold_digest": False, "reason": "dedupe"}

        if type_name in _prefs.ALWAYS_ON:
            return {"deliver": True, "hold_digest": False, "reason": "always on"}

        limit = int(self._prefs.get("rate_limit_per_hour", 20) or 0)
        sent = [t for t in (st.get("sent_log") or []) if now - float(t) < 3600]
        if limit and len(sent) >= limit:
            return {"deliver": False, "hold_digest": True,
                    "reason": "rate limited (rolled into digest)"}

        if mode == "digest":
            return {"deliver": False, "hold_digest": True, "reason": "digest mode"}

        if _prefs.in_quiet_hours(self._prefs, _now_local(self._clock_fn())):
            return {"deliver": False, "hold_digest": True, "reason": "quiet hours"}

        return {"deliver": True, "hold_digest": False, "reason": "ok"}

    def record_sent(self, now: float | None = None) -> None:
        st = _prefs.load_state()
        ts = now if now is not None else self._clock_fn()
        st.setdefault("sent_log", []).append(ts)
        st["sent_log"] = [t for t in st["sent_log"] if ts - float(t) < 3700][-200:]
        _prefs.save_state()

    def mark_seen(self, dedupe_key: str) -> None:
        st = _prefs.load_state()
        dedupe = st.setdefault("dedupe", {})
        dedupe[dedupe_key] = self._clock_fn()
        if len(dedupe) > 512:
            newest = sorted(dedupe.items(), key=lambda kv: kv[1])[-256:]
            st["dedupe"] = dict(newest)
        _prefs.save_state()


def _now_local(epoch: float):
    from datetime import datetime
    return datetime.fromtimestamp(epoch)
