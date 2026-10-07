"""Mission steering commands (roadmap 20D).

Parses and dispatches /pause /resume /stop /redirect for missions. Business
logic stays in the Mission Engine; this only parses the operator command and
posts the steering request (injectable ``post`` for tests).
"""
from __future__ import annotations

STEER_ACTIONS = {"pause", "resume", "redirect"}
EXECUTE_ACTIONS = {"stop"}
ALL_ACTIONS = STEER_ACTIONS | EXECUTE_ACTIONS
ENDPOINT = {a: "steer" for a in STEER_ACTIONS}
ENDPOINT.update({a: "execute" for a in EXECUTE_ACTIONS})


def parse_steer_command(text: str):
    """Return (action, mission_id) or (None, None) if not a steering command."""
    parts = (text or "").strip().split()
    if not parts:
        return (None, None)
    action = parts[0].lstrip("/").lower()
    if action not in ALL_ACTIONS:
        return (None, None)
    mission_id = None
    for token in parts[1:]:
        if token.startswith("msn_"):
            mission_id = token
            break
    if mission_id is None and len(parts) > 1:
        mission_id = parts[1]
    return (action, mission_id)


def _default_post(path: str, payload: dict):
    import json
    import ssl
    from urllib import request as _urlreq

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    data = json.dumps(payload).encode()
    req = _urlreq.Request("https://127.0.0.1:8000" + path, data=data,
                          method="POST",
                          headers={"Content-Type": "application/json"})
    try:
        with _urlreq.urlopen(req, timeout=8, context=ctx) as resp:
            return (resp.status, json.loads(resp.read().decode() or "{}"))
    except Exception as exc:
        return (0, str(exc))


def handle_steer_command(text: str, *, post=None) -> str:
    action, mission_id = parse_steer_command(text)
    if action is None:
        return ""
    if not mission_id:
        return f"usage: /{action} <mission_id> (e.g. /{action} msn_abc123)"
    poster = post or _default_post
    endpoint = ENDPOINT[action]
    status, data = poster(f"/kai/missions/{mission_id}/{endpoint}",
                          {"action": action})
    if status and 200 <= int(status) < 300:
        return f"{action} accepted for {mission_id}"
    return f"{action} failed for {mission_id} (status={status}: {data})"
