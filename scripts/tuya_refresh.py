#!/usr/bin/env python3
"""Keep the Tuya (Smart Life) cloud session fresh and persisted.

tuya_sharing exposes SharingTokenListener.update_token; we register it so the
SDK's own refresh is written back to the encrypted vault. Runs as a systemd
timer; if the session has fully expired (1010), it logs a clear "re-scan
required" signal and exits non-zero WITHOUT fabricating anything.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/opt/ai-orchestrator")

from core.ai.credential_vault import retrieve_credential, store_credential  # noqa: E402

CLIENT_ID = "HA_3y9q4ak7g4ephrvke"


def _load() -> dict:
    c = retrieve_credential("tuya_cloud") or {}
    return json.loads(c.get("api_key") or "{}")


class _Listener:
    """Persist any token the SDK refreshes back into the vault."""

    def __init__(self, session: dict):
        self.session = session

    def update_token(self, token_info: dict) -> None:
        self.session.update(token_info or {})
        store_credential("tuya_cloud", json.dumps(self.session),
                         api_base=self.session.get("endpoint", ""))
        print("token refreshed + persisted:", sorted((token_info or {}).keys()))

    # compatibility alias some SDK builds call
    def token_updated_callback(self, token_info: dict) -> None:  # noqa: D401
        self.update_token(token_info)

    def on_logout(self) -> None:
        print("LOGOUT: session invalidated by Tuya")


def main() -> int:
    s = _load()
    if not s.get("access_token") or not s.get("uid"):
        print("no tuya_cloud session stored")
        return 2
    try:
        from tuya_sharing import Manager
    except Exception as e:  # noqa: BLE001
        print("tuya_sharing not installed:", e)
        return 3
    listener = _Listener(s)
    try:
        m = Manager(CLIENT_ID, s["uid"], s["terminal_id"], s["endpoint"], s, listener)
        m.refresh_mq()
        m.update_device_cache()
        print("devices:", len(m.device_map))
        # persist whatever the manager now holds (refreshed)
        store_credential("tuya_cloud", json.dumps(s), api_base=s.get("endpoint", ""))
        print("session OK")
        return 0
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "1010" in msg or "expired" in msg.lower() or "sign invalid" in msg:
            print("SESSION_EXPIRED: re-scan required (see docs/smarthome/TUYA_SESSION_EXPIRY.md)")
        else:
            print("refresh error:", type(e).__name__, msg[:160])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
