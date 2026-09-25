#!/usr/bin/env python3
"""Persistent GitHub device-flow poller for HACS (KAI).

Runs forever as a systemd service: obtains a device code, prints it, and polls
GitHub continuously (handles authorization_pending/slow_down, refreshes the code
when it expires). On success writes the access token to /tmp/opencode/gh_token.txt
and exits 0. The token is then used to seed HACS's config entry directly.

This replaces the fragile transient pollers: the operator can enter the code at
any time within the window and it will be honoured.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = "395a8e669c5de9f7c6e8"   # HACS' public GitHub OAuth app id
UA = "KAI-HACS"
CODE_FILE = "/tmp/opencode/gh_code.txt"
TOKEN_FILE = "/tmp/opencode/gh_token.txt"
STATUS_FILE = "/tmp/opencode/gh_status.txt"


def _post(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Accept": "application/json", "User-Agent": UA})
    return json.load(urllib.request.urlopen(req, timeout=30))


def _write(path: str, text: str) -> None:
    with open(path, "w") as fh:
        fh.write(text)


def main() -> int:
    while True:
        try:
            d = _post("https://github.com/login/device/code",
                      {"client_id": CLIENT_ID, "scope": "repo"})
        except Exception as e:  # noqa: BLE001
            _write(STATUS_FILE, f"code request failed: {type(e).__name__}")
            time.sleep(15)
            continue

        code = d.get("user_code")
        device = d["device_code"]
        _write(CODE_FILE, code or "")
        _write(STATUS_FILE, f"awaiting {code} at {d.get('verification_uri')}")
        print(f"USER_CODE={code} URI={d.get('verification_uri')}", flush=True)

        deadline = time.time() + int(d.get("expires_in", 900))
        interval = max(5, int(d.get("interval", 5)))
        while time.time() < deadline:
            time.sleep(interval)
            try:
                r = _post("https://github.com/login/oauth/access_token",
                          {"client_id": CLIENT_ID, "device_code": device,
                           "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            except Exception as e:  # noqa: BLE001
                print("poll err", type(e).__name__, flush=True)
                continue
            if r.get("access_token"):
                _write(TOKEN_FILE, r["access_token"])
                _write(STATUS_FILE, "TOKEN_OK")
                print("TOKEN_OK", flush=True)
                return 0
            err = r.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err == "expired_token":
                _write(STATUS_FILE, "code expired; issuing a new one")
                print("EXPIRED -> new code", flush=True)
                break
            print("ERROR", r, flush=True)
            _write(STATUS_FILE, f"error {err}")
            time.sleep(10)
        # loop -> issue a fresh code automatically


if __name__ == "__main__":
    raise SystemExit(main())
