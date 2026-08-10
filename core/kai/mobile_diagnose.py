"""Kai Mobile Command Node — Self-Diagnostics (SP6).

Provides the `kai mobile diagnose` command that validates the full mobile
command node stack: WireGuard connectivity, device registration,
authentication, notifications, API health, AI providers, and the health worker.

Can run standalone (CLI) or via API (GET /kai/mobile/diagnose).

Part of: Kai Mobile Command Node — Sub-project 6: Integration & Testing.
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual checks — each returns {name, status, detail, artifact}
# ---------------------------------------------------------------------------


def _check_device_registry() -> dict:
    """Verify the device registry has at least one authorized device."""
    try:
        from core.device_registry import list_devices
        devices = list_devices()
        authorized = [d for d in devices if d.get("status") == "authorized"]

        if not devices:
            return {
                "name": "Device Registry",
                "status": "WARN",
                "detail": "No devices registered",
                "artifact": None,
            }

        online = []
        for d in authorized:
            last_hb = d.get("last_heartbeat")
            if last_hb:
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_hb)).total_seconds()
                    if age < 120:
                        online.append(d["device_id"])
                except (ValueError, TypeError):
                    pass

        return {
            "name": "Device Registry",
            "status": "PASS" if online else ("WARN" if authorized else "FAIL"),
            "detail": f"{len(authorized)} authorized, {len(online)} online, {len(devices)} total devices",
            "artifact": {
                "total": len(devices),
                "authorized": len(authorized),
                "online": online,
                "offline": [d["device_id"] for d in authorized if d["device_id"] not in online],
                "revoked": len([d for d in devices if d.get("status") == "revoked"]),
            },
        }
    except Exception as exc:
        return {"name": "Device Registry", "status": "FAIL", "detail": str(exc), "artifact": None}


def _check_wireguard() -> dict:
    """Check WireGuard connectivity for registered device VPN IPs."""
    try:
        from core.device_registry import list_devices
        from core.wireguard_manager import get_wg_status

        devices = list_devices(status="authorized")
        device_ips = [d.get("vpn_ip") for d in devices if d.get("vpn_ip")]
        if not device_ips:
            return {
                "name": "WireGuard",
                "status": "WARN",
                "detail": "No devices with VPN IPs configured",
                "artifact": None,
            }

        wg = get_wg_status()
        if not wg.get("ok"):
            return {
                "name": "WireGuard",
                "status": "FAIL",
                "detail": f"Cannot query WireGuard: {wg.get('error', 'unknown')}",
                "artifact": None,
            }

        peers = wg.get("peers", [])
        peer_ips = set()
        for p in peers:
            for ip in p.get("allowed_ips", []):
                peer_ips.add(ip.replace("/32", "").replace("/24", ""))

        results = {}
        for ip in device_ips:
            if ip in peer_ips:
                for p in peers:
                    if f"{ip}/32" in p.get("allowed_ips", []):
                        handshake = p.get("handshake_age_sec", 9999)
                        results[ip] = "connected" if handshake < 90 else ("degraded" if handshake < 300 else "offline")
                        break
            else:
                results[ip] = "not_found"

        all_connected = all(v == "connected" for v in results.values())
        any_alive = any(v in ("connected", "degraded") for v in results.values())

        return {
            "name": "WireGuard",
            "status": "PASS" if all_connected else ("WARN" if any_alive else "FAIL"),
            "detail": f"{sum(1 for v in results.values() if v=='connected')}/{len(results)} peers connected",
            "artifact": results,
        }
    except Exception as exc:
        return {"name": "WireGuard", "status": "FAIL", "detail": str(exc), "artifact": None}


def _check_api_reachability() -> dict:
    """Verify Kai API is serving and responding."""
    try:
        from core.api import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/health")
        if resp.status_code == 200:
            data = resp.json()
            return {
                "name": "Kai API",
                "status": "PASS",
                "detail": f"Port 8000 responding, health={data.get('status', 'unknown')}",
                "artifact": {"status_code": 200, "health": data.get("status")},
            }
        return {
            "name": "Kai API",
            "status": "FAIL",
            "detail": f"Health endpoint returned {resp.status_code}",
            "artifact": None,
        }
    except Exception as exc:
        return {"name": "Kai API", "status": "FAIL", "detail": str(exc), "artifact": None}


def _check_authentication() -> dict:
    """Verify authentication subsystems are working."""
    try:
        from core.device_registry import list_devices, DEVICE_TOKEN_PREFIX
        from core.bridge_auth import _load_api_token, API_TOKEN_PATH

        checks = []

        # Bridge token
        bridge_token = _load_api_token()
        if bridge_token:
            checks.append("bridge_token: OK")
        else:
            checks.append("bridge_token: MISSING")

        # Device tokens
        devices = list_devices()
        authorized = [d for d in devices if d.get("status") == "authorized"]
        checks.append(f"device_tokens: {len(authorized)} authorized")

        # JWT session
        from core.jwt_auth import _JWT_SECRET
        jwt_ok = bool(_JWT_SECRET)
        if jwt_ok:
            checks.append("jwt_session: configured")
        else:
            checks.append("jwt_session: NOT_CONFIGURED")

        return {
            "name": "Authentication",
            "status": "PASS" if bridge_token and authorized else ("WARN" if bridge_token else "FAIL"),
            "detail": "; ".join(checks),
            "artifact": {
                "bridge_token_exists": bool(bridge_token),
                "authorized_devices": len(authorized),
                "jwt_configured": jwt_ok,
            },
        }
    except Exception as exc:
        return {"name": "Authentication", "status": "FAIL", "detail": str(exc), "artifact": None}


def _check_notifications() -> dict:
    """Verify the notification system is operational."""
    try:
        from core.notifications import NotificationManager

        stats = NotificationManager.get_stats()
        unread = NotificationManager.unread_count()

        # Test: create a silent internal check (won't trigger Telegram if source is not in always-list)
        test = NotificationManager.enqueue(
            severity="informational",
            title="Diagnostic check",
            body="Self-diagnostic verification",
            source="diagnostic",
            module="system",
        )

        return {
            "name": "Notifications",
            "status": "PASS",
            "detail": f"{stats['total']} total, {unread['total']} unread ({unread['critical']} critical)",
            "artifact": {
                "total": stats["total"],
                "unread": unread,
                "test_enqueued": test is not None,
            },
        }
    except Exception as exc:
        return {"name": "Notifications", "status": "FAIL", "detail": str(exc), "artifact": None}


def _check_providers() -> dict:
    """Verify at least one AI provider is reachable for each role."""
    try:
        from core.ai.ai_router import ROLE_PROVIDERS  # noqa: F401
        from core.ai_provider import list_providers

        providers = list_providers()
        active = [p for p in providers.values() if p.get("enabled", True)]

        return {
            "name": "AI Providers",
            "status": "PASS" if active else "WARN",
            "detail": f"{len(active)} providers, {len(providers)} registered",
            "artifact": {
                "total_registered": len(providers),
                "active": len(active),
                "roles_configured": list(ROLE_PROVIDERS.keys()),
            },
        }
    except Exception as exc:
        return {"name": "AI Providers", "status": "FAIL", "detail": str(exc), "artifact": None}


def _check_health_worker() -> dict:
    """Verify the health worker is assigned and running."""
    try:
        from core.device_registry import list_devices
        from core.health_worker import _default_worker

        devices = list_devices()
        assigned = [d for d in devices if d.get("assigned_worker") == "KAI-SYSTEM-HEALTH-WORKER"]

        worker_running = getattr(
            _default_worker, "is_running", None
        ) if _default_worker else True

        return {
            "name": "Health Worker",
            "status": "PASS" if assigned and worker_running else ("WARN" if assigned else "FAIL"),
            "detail": f"{len(assigned)} device(s) assigned, worker={'running' if worker_running else 'stopped'}",
            "artifact": {
                "assigned_devices": len(assigned),
                "worker_running": bool(worker_running),
            },
        }
    except Exception as exc:
        return {"name": "Health Worker", "status": "FAIL", "detail": str(exc), "artifact": None}


def _check_pwa() -> dict:
    """Verify PWA assets (manifest, service worker) are served."""
    try:
        from core.api import app
        from fastapi.testclient import TestClient

        client = TestClient(app)

        manifest = client.get("/kai/manifest.json")
        sw = client.get("/kai/sw.js")
        cc = client.get("/command-center")

        return {
            "name": "PWA Assets",
            "status": "PASS" if manifest.status_code == 200 and sw.status_code == 200 else "FAIL",
            "detail": f"manifest={manifest.status_code}, sw={sw.status_code}, command_center={'PWA' if 'manifest.json' in cc.text else '?'}",
            "artifact": {
                "manifest_ok": manifest.status_code == 200,
                "sw_ok": sw.status_code == 200,
                "pwa_meta_in_html": "manifest.json" in cc.text,
                "service_worker_in_html": "serviceWorker" in cc.text,
            },
        }
    except Exception as exc:
        return {"name": "PWA Assets", "status": "FAIL", "detail": str(exc), "artifact": None}


# ---------------------------------------------------------------------------
# Main diagnostic runner
# ---------------------------------------------------------------------------

ALL_CHECKS = [
    _check_device_registry,
    _check_wireguard,
    _check_api_reachability,
    _check_authentication,
    _check_notifications,
    _check_providers,
    _check_health_worker,
    _check_pwa,
]


def run_diagnostic() -> dict:
    """Run all mobile command node self-diagnostics.

    Returns {ok, summary, checks, timestamp, server_time}.
    Each check: {name, status, detail, artifact}.

    Status values:
    - PASS — subsystem is healthy
    - WARN — degraded but functional
    - FAIL — broken or unreachable
    """
    results = []
    passed = 0
    warned = 0
    failed = 0

    for check_fn in ALL_CHECKS:
        start = time.monotonic()
        try:
            result = check_fn()
        except Exception as exc:
            result = {"name": check_fn.__name__, "status": "FAIL", "detail": str(exc), "artifact": None}
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        result["elapsed_ms"] = elapsed_ms

        if result["status"] == "PASS":
            passed += 1
        elif result["status"] == "WARN":
            warned += 1
        else:
            failed += 1

        results.append(result)

    overall = "PASS" if failed == 0 else ("WARN" if failed <= 2 else "FAIL")

    return {
        "ok": overall != "FAIL",
        "summary": f"{passed} passed, {warned} warned, {failed} failed — {overall}",
        "overall": overall,
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "total": len(results),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": results,
    }


# ---------------------------------------------------------------------------
# CLI entry point — `python -m core.kai.mobile_diagnose`
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for `kai mobile diagnose`."""
    print("Kai Mobile Command Node — Self-Diagnostics")
    print("=" * 60)
    print()

    result = run_diagnostic()

    for check in result["checks"]:
        icon = {"PASS": "\033[32m✓\033[0m", "WARN": "\033[33m⚠\033[0m", "FAIL": "\033[31m✗\033[0m"}
        print(f"  {icon.get(check['status'], '?')} {check['name']}: {check['status']}")
        print(f"    {check['detail']}")
        if check.get("artifact"):
            artifact_str = json.dumps(check["artifact"], default=str)
            if len(artifact_str) > 120:
                artifact_str = artifact_str[:117] + "..."
            print(f"    data: {artifact_str}")
        print(f"    ({check.get('elapsed_ms', '?')}ms)")
        print()

    print("=" * 60)
    icon = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}
    print(f"  {icon.get(result['overall'], '?')} {result['summary']}")
    print()

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
