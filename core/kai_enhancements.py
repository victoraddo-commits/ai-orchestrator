"""KAI Enhancement Registry — hardware-gated optional capabilities (§4/§46/§47).

Each enhancement declares:
  - the hardware/service it REQUIRES (probe function)
  - whether it's user-ENABLED (explicit opt-in, never auto)
  - effective state: ENABLED only when enabled AND requirements met

A user can enable an enhancement whose hardware isn't present — it will
report ENABLED but BLOCKED with the exact missing requirement, and auto-
activate the moment the probe passes. Nothing here is on by default.

Registry is persisted to memory/kai_enhancements.json.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_MEMORY_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "memory"
STATE_PATH = _MEMORY_DIR / "kai_enhancements.json"

# ---------------------------------------------------------------------------
# Requirement probes — honest checks against real hardware/services.
# Each returns (met: bool, detail: str).
# ---------------------------------------------------------------------------

def _req_always_on_audio() -> tuple[bool, str]:
    """Wake word needs a device with a microphone that runs 24/7 near the user."""
    try:
        import requests
        # The voice server can synthesize/transcribe but wake-word detection
        # lives ON A CLIENT DEVICE. Probe: has any mobile/desktop client
        # registered as always-on? (device registry kind='voice_client')
        from core.device_registry import list_devices
        devices = list_devices() if callable(list_devices) else []
        voice_clients = [d for d in devices if isinstance(d, dict)
                         and d.get("kind") == "voice_client"]
        if voice_clients:
            return True, f"{len(voice_clients)} voice client device(s) registered"
        return False, ("requires an always-on microphone device (phone app with "
                       "'wake word' enabled, or desktop agent). No voice client "
                       "registered yet.")
    except Exception as e:
        return False, f"cannot check devices: {type(e).__name__}"


def _req_ha_server() -> tuple[bool, str]:
    """Home Assistant integration requires a reachable HA instance + token."""
    url = os.environ.get("HA_BASE_URL", "")
    tok = os.environ.get("HA_TOKEN", "")
    if not url or not tok:
        return False, ("requires a Home Assistant server: set HA_BASE_URL "
                       "(e.g. http://<ha-ip>:8123) and HA_TOKEN (long-lived "
                       "access token) in ai-orchestrator .env")
    try:
        import requests
        r = requests.get(f"{url.rstrip('/')}/api/", headers={"Authorization": f"Bearer {tok}"}, timeout=5)
        if r.status_code == 200:
            return True, f"HA reachable at {url}"
        return False, f"HA responded {r.status_code} — check token"
    except Exception as e:
        return False, f"HA unreachable at {url}: {type(e).__name__}"


def _req_telephony() -> tuple[bool, str]:
    """Inbound calls need a SIP trunk / phone number provider."""
    sid = os.environ.get("TELEPHONY_PROVIDER", "")
    if not sid:
        return False, ("requires a telephony provider (SIP trunk or API like "
                       "Twilio/Telnyx): set TELEPHONY_PROVIDER, plus its "
                       "credentials in .env")
    return True, f"telephony provider configured: {sid}"


def _req_gpu_stt() -> tuple[bool, str]:
    """Higher-quality streaming STT wants GPU; CPU fallback works degraded."""
    try:
        import requests
        r = requests.get("http://192.168.1.109:8130/health", timeout=3)
        if r.status_code == 200:
            return True, "voice server up (CPU mode — streaming quality reduced)"
        return False, "voice server down"
    except Exception:
        return False, "voice server unreachable at 192.168.1.109:8130"


# ---------------------------------------------------------------------------
# Enhancement definitions
# ---------------------------------------------------------------------------

ENHANCEMENTS = {
    "wake_word": {
        "name": "Wake Word",
        "description": "Always-on 'KAI' hotword on a nearby device; hands-free voice.",
        "spec_section": "§5",
        "requires": [_req_always_on_audio],
        "provides": ["kai.voice.wake_word"],
    },
    "streaming_voice": {
        "name": "Streaming Voice",
        "description": "Realtime STT with barge-in/interruption instead of record-stop-send.",
        "spec_section": "§5",
        "requires": [_req_gpu_stt],
        "provides": ["kai.voice.streaming"],
    },
    "home_assistant": {
        "name": "Home Assistant",
        "description": "Control lights/thermostat/sensors/cameras via your HA server.",
        "spec_section": "§46",
        "requires": [_req_ha_server],
        "provides": ["kai.home.control"],
    },
    "telephony": {
        "name": "Telephony",
        "description": "Inbound phone calls to Kai with caller authentication.",
        "spec_section": "§47",
        "requires": [_req_telephony],
        "provides": ["kai.phone.inbound"],
    },
}


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, default=str))
    os.replace(tmp, STATE_PATH)


def status(include_all: bool = True) -> dict:
    """Full enhancement status: enabled × requirements = effective state."""
    saved = _load_state()
    out = {}
    for key, defn in ENHANCEMENTS.items():
        user_enabled = bool(saved.get(key, {}).get("enabled"))
        req_results = [probe() for probe in defn["requires"]]
        all_met = all(met for met, _ in req_results)
        missing = [detail for met, detail in req_results if not met]
        if user_enabled and all_met:
            state = "ENABLED"
        elif user_enabled:
            state = "BLOCKED"
        else:
            state = "DISABLED"
        out[key] = {
            "name": defn["name"],
            "description": defn["description"],
            "spec": defn["spec_section"],
            "user_enabled": user_enabled,
            "requirements_met": all_met,
            "missing": missing,
            "state": state,
            "enabled_at": saved.get(key, {}).get("enabled_at"),
        }
    return out


def enable(key: str, operator: str = "operator") -> dict:
    """User opt-in. May be done BEFORE hardware exists → state becomes
    BLOCKED and flips to ENABLED automatically once probes pass."""
    if key not in ENHANCEMENTS:
        return {"ok": False, "error": f"unknown enhancement '{key}'",
                "available": sorted(ENHANCEMENTS.keys())}
    state = _load_state()
    state.setdefault(key, {})
    state[key]["enabled"] = True
    state[key]["enabled_by"] = operator
    state[key]["enabled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_state(state)
    st = status().get(key, {})
    return {"ok": True, "enhancement": key, "state": st["state"],
            "missing_requirements": st["missing"],
            "note": ("will auto-activate when requirements are met"
                     if st["state"] == "BLOCKED" else "active now")}


def disable(key: str, operator: str = "operator") -> dict:
    if key not in ENHANCEMENTS:
        return {"ok": False, "error": f"unknown enhancement '{key}'"}
    state = _load_state()
    state.setdefault(key, {})
    state[key]["enabled"] = False
    _save_state(state)
    return {"ok": True, "enhancement": key, "state": "DISABLED"}


def capability_available(capability_id: str) -> bool:
    """The one call other code makes: can I use kai.voice.wake_word etc.?"""
    st = status()
    for info in st.values():
        pass
    for key, defn in ENHANCEMENTS.items():
        if capability_id in defn["provides"]:
            info = st.get(key, {})
            return info.get("state") == "ENABLED"
    return False
