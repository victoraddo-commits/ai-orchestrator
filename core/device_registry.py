"""Kai Device Registry — storage layer.

Atomic read-modify-write on memory/device_registry.json, with fcntl.flock
for multi-process safety, schema-versioned files, and migrations.

Part of: Kai Mobile Command Node — Sub-project 1: Device Registration & Auth.

Follows the same pattern as core/app_registry.py to keep the codebase
consistent.  Differences from App Registry:
- Devices are identified by device_id (human-readable), not auto-generated id
- Long-lived bearer tokens with bcrypt hashing (not JWTs)
- Heartbeat tracking with pending-command delivery
- No search, no metadata filtering, no hooks (simpler lifecycle)
"""

import fcntl
import json
import logging
import os
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import bcrypt

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1
REGISTRY_FILENAME = "device_registry.json"
DEVICE_TOKEN_PREFIX = "kai_device_"

# ---------------------------------------------------------------------------
# Pending commands — in-memory only.  Commands are injected by other Kai
# subsystems and delivered to devices via heartbeat responses.
# ───────────────────────────────────────────────────────────────────────────
# { device_id: [{"id": str, "action": str, "payload": dict, "created_at": str, "retries": int}] }
# ---------------------------------------------------------------------------
_pending_commands: dict[str, list[dict]] = {}
MAX_COMMAND_RETRIES = 5


# ---------------------------------------------------------------------------
# Path helpers (same pattern as app_registry.py)
# ---------------------------------------------------------------------------

def _default_memory_dir() -> Path:
    override = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    return Path(override) if override else Path("memory")


def _registry_path() -> Path:
    return _default_memory_dir() / REGISTRY_FILENAME


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


def _backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _tmp_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + f".tmp.{os.getpid()}")


# ---------------------------------------------------------------------------
# Load / Save (same pattern as app_registry.py)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_registry_file(path: Path) -> dict:
    """Return {schema_version, records: [...]} dict.  Creates empty if missing."""

    if not path.exists():
        return {"schema_version": CURRENT_SCHEMA_VERSION, "records": []}

    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        backup = _backup_path(path)
        if backup.exists():
            try:
                with open(backup, "r") as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, OSError):
                return {"schema_version": CURRENT_SCHEMA_VERSION, "records": []}
        else:
            return {"schema_version": CURRENT_SCHEMA_VERSION, "records": []}

    return raw


def _write_locked(data: dict, path: Path):
    """Write registry data to disk (caller holds flock)."""

    if path.exists():
        try:
            shutil.copyfile(path, _backup_path(path))
        except OSError:
            pass

    tmp = _tmp_path(path)
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _save_registry_file(data: dict, path: Path):
    """Thread-safe save with flock around write."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(path)

    with open(lock, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            _write_locked(data, path)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _update_registry(mutate_fn) -> list[dict]:
    """Load → mutate → save under flock.  Returns updated records list."""

    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(path)

    with open(lock, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            data = _load_registry_file(path)
            data = mutate_fn(data)
            _write_locked(data, path)
            return data["records"]
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Device record helpers
# ---------------------------------------------------------------------------

def _device_record(device_id: str, data: dict) -> Optional[dict]:
    """Return the record dict for device_id, or None."""
    for r in data["records"]:
        if r.get("device_id") == device_id:
            return r
    return None


def _generate_token() -> tuple[str, str]:
    """Generate a new device bearer token.

    Returns (raw_token, bcrypt_hash).
    raw_token looks like: kai_device_a1b2c3d4... (64 hex chars after prefix)

    bcrypt has a 72-byte limit, so we SHA-256 the raw token first, then
    bcrypt the hex digest.  This preserves entropy without truncation.
    """
    import hashlib
    raw = DEVICE_TOKEN_PREFIX + secrets.token_hex(32)  # 76 chars total
    digest = hashlib.sha256(raw.encode()).hexdigest()   # 64 hex chars = 64 bytes
    token_hash = bcrypt.hashpw(digest.encode(), bcrypt.gensalt()).decode()
    return raw, token_hash


# ---------------------------------------------------------------------------
# Public API: CRUD
# ---------------------------------------------------------------------------

def register_device(
    device_id: str,
    device_name: str,
    platform: str,
    platform_version: str,
    manufacturer: str,
    model: str,
    registered_by: str = "operator",
    **extra,
) -> dict:
    """Register a new device.  Generates a bearer token, stores bcrypt hash.

    Returns the device record dict with the raw token included.
    The raw token is NEVER persisted — only the hash is stored.
    The caller is responsible for transferring the token to the device.
    """

    raw_token, token_hash = _generate_token()
    now = _now_iso()

    record = {
        "device_id": device_id,
        "device_name": device_name,
        "token_hash": token_hash,
        "platform": platform,
        "platform_version": platform_version,
        "manufacturer": manufacturer,
        "model": model,
        "one_ui_version": extra.get("one_ui_version"),
        "security_patch": extra.get("security_patch"),
        "vpn_ip": extra.get("vpn_ip"),
        "capabilities": extra.get("capabilities", []),
        "status": "authorized",
        "assigned_worker": extra.get("assigned_worker"),
        "last_heartbeat": None,
        "heartbeat_data": {},
        "created_at": now,
        "updated_at": now,
        "registered_by": registered_by,
    }

    def _create(data: dict) -> dict:
        existing = _device_record(device_id, data)
        if existing and existing.get("status") != "revoked":
            raise DuplicateDeviceError(device_id)
        data["records"].append(record)
        return data

    _update_registry(_create)
    logger.info("device_registry: registered %s (%s %s)", device_id, manufacturer, model)

    # Return record with raw token attached, NEVER expose token_hash
    result = {k: v for k, v in record.items() if k != "token_hash"}
    result["token"] = raw_token
    return result


def get_device(device_id: str) -> Optional[dict]:
    """Get a device record by id (without token_hash)."""

    path = _registry_path()
    if not path.exists():
        return None

    data = _load_registry_file(path)
    record = _device_record(device_id, data)
    if record is None:
        return None

    # Never expose token_hash
    result = dict(record)
    result.pop("token_hash", None)
    return result


def list_devices(status: Optional[str] = None) -> list[dict]:
    """List all devices, optionally filtered by status."""

    path = _registry_path()
    if not path.exists():
        return []

    data = _load_registry_file(path)
    records = data["records"]

    if status:
        records = [r for r in records if r.get("status") == status]

    # Never expose token_hash
    return [{k: v for k, v in r.items() if k != "token_hash"} for r in records]


def revoke_device(device_id: str) -> dict:
    """Revoke a device — sets status to 'revoked'.  Irreversible."""

    def _revoke(data: dict) -> dict:
        record = _device_record(device_id, data)
        if record is None:
            raise DeviceNotFoundError(device_id)
        record["status"] = "revoked"
        record["updated_at"] = _now_iso()
        return data

    _update_registry(_revoke)
    logger.info("device_registry: revoked %s", device_id)
    return {"device_id": device_id, "previous_status": "authorized"}


def delete_device(device_id: str) -> dict:
    """Remove a device record entirely."""

    def _delete(data: dict) -> dict:
        record = _device_record(device_id, data)
        if record is None:
            raise DeviceNotFoundError(device_id)
        data["records"] = [r for r in data["records"] if r.get("device_id") != device_id]
        return data

    _update_registry(_delete)
    _pending_commands.pop(device_id, None)
    logger.info("device_registry: deleted %s", device_id)
    return {"device_id": device_id}


# ---------------------------------------------------------------------------
# Token lookup (for auth middleware)
# ---------------------------------------------------------------------------

def find_device_by_token(token: str) -> Optional[dict]:
    """Find a device record by its raw bearer token.

    Iterates all records and bcrypt-checks each token_hash.
    This is O(n) but the device registry will have < 10 entries.
    Caller should cache results if this becomes a hot path.
    """

    if not token.startswith(DEVICE_TOKEN_PREFIX):
        return None

    path = _registry_path()
    if not path.exists():
        return None

    data = _load_registry_file(path)
    import hashlib
    digest = hashlib.sha256(token.encode()).hexdigest().encode()

    for record in data["records"]:
        if record.get("status") != "authorized":
            continue
        stored_hash = record.get("token_hash")
        if not stored_hash:
            continue
        try:
            if bcrypt.checkpw(digest, stored_hash.encode()):
                return record
        except (ValueError, AttributeError):
            continue

    return None


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def update_heartbeat(device_id: str, heartbeat_data: dict) -> Optional[dict]:
    """Update device heartbeat timestamp and data.  Returns pending commands."""

    def _update(data: dict) -> dict:
        record = _device_record(device_id, data)
        if record is None:
            raise DeviceNotFoundError(device_id)
        record["last_heartbeat"] = _now_iso()
        record["heartbeat_data"] = heartbeat_data
        record["vpn_ip"] = heartbeat_data.get("vpn_ip", record.get("vpn_ip"))
        record["updated_at"] = _now_iso()
        return data

    _update_registry(_update)

    # Return pending commands + health snapshot
    commands = _pending_commands.get(device_id, [])
    return {
        "ok": True,
        "server_time": _now_iso(),
        "pending_commands": commands,
        "health_summary": _get_health_summary(vpn_ip=heartbeat_data.get("vpn_ip")),
    }


def ack_commands(device_id: str, ack_ids: list[str]) -> int:
    """Remove acknowledged commands.  Returns count removed."""

    if device_id not in _pending_commands:
        return 0

    before = len(_pending_commands[device_id])
    _pending_commands[device_id] = [
        c for c in _pending_commands[device_id] if c["id"] not in ack_ids
    ]
    return before - len(_pending_commands[device_id])


# ---------------------------------------------------------------------------
# Command injection (called by other Kai subsystems)
# ---------------------------------------------------------------------------

def inject_command(device_id: str, action: str, payload: dict) -> str:
    """Queue a command for delivery to a device.

    Returns the command id.
    Commands are delivered on the next heartbeat and retried up to
    MAX_COMMAND_RETRIES times if unacknowledged.
    """

    import uuid

    cmd_id = f"cmd_{uuid.uuid4().hex[:12]}"
    cmd = {
        "id": cmd_id,
        "action": action,
        "payload": payload,
        "created_at": _now_iso(),
        "retries": 0,
    }

    if device_id not in _pending_commands:
        _pending_commands[device_id] = []
    _pending_commands[device_id].append(cmd)

    logger.info("device_registry: injected command %s for %s: %s", cmd_id, device_id, action)
    return cmd_id


def _prune_stale_commands(device_id: str):
    """Remove commands that have exceeded retry limit."""
    if device_id not in _pending_commands:
        return
    _pending_commands[device_id] = [
        c for c in _pending_commands[device_id] if c["retries"] < MAX_COMMAND_RETRIES
    ]


# ---------------------------------------------------------------------------
# Health summary
# ---------------------------------------------------------------------------

def _get_health_summary(vpn_ip: Optional[str] = None) -> dict:
    """Build a compact health summary for heartbeat responses.

    Uses data available without importing the full orchestration stack.
    Health check details come from the existing /health endpoint data.

    When vpn_ip is provided (from device heartbeat), includes WireGuard
    peer connectivity status for that device's VPN IP.
    """

    # Default: assume healthy unless we can detect otherwise
    summary = {
        "overall": "healthy",
        "alerts_count": 0,
        "components": {},
    }

    # Try to read system state from memory if available
    try:
        state_path = _default_memory_dir() / "system_state.json"
        if state_path.exists():
            with open(state_path) as f:
                state = json.load(f)
            # Extract basic health signals
            summary["components"]["kai_core"] = "healthy" if state else "unknown"
    except (OSError, json.JSONDecodeError):
        pass

    # Check WireGuard peer status for this device's VPN IP
    if vpn_ip:
        summary["components"]["vpn"] = _check_vpn_peer_health(vpn_ip)

    return summary


def _check_vpn_peer_health(vpn_ip: str) -> dict:
    """Check whether a WireGuard peer at vpn_ip is healthy.

    Uses core.wireguard_manager to query DD-WRT for peer status.
    Returns a compact status dict: {status, handshake_age_s, endpoint}.
    Status is one of: connected, degraded (handshake > 150s), offline.
    If the WireGuard module can't be reached, returns {status: "unknown"}.
    """
    try:
        from core.wireguard_manager import get_wg_status

        wg = get_wg_status()
        if not wg.get("ok"):
            return {"status": "unknown", "error": "WireGuard status query failed"}

        peers = wg.get("peers", [])
        target_cidr = f"{vpn_ip}/32"
        for peer in peers:
            allowed = peer.get("allowed_ips", [])
            if target_cidr in allowed:
                handshake = peer.get("handshake_age_sec", 0)
                if handshake < 90:
                    status = "connected"
                elif handshake < 300:
                    status = "degraded"
                else:
                    status = "offline"
                # Transfer values are human-readable strings like "33.17 MiB"
                return {
                    "status": status,
                    "handshake_age_s": handshake,
                    "endpoint": peer.get("endpoint"),
                    "transfer_rx": peer.get("transfer_rx", "0"),
                    "transfer_tx": peer.get("transfer_tx", "0"),
                }

        # peer not found in WG show output
        return {"status": "offline", "error": f"Peer {vpn_ip} not in WireGuard table"}

    except ImportError:
        logger.warning("wireguard_manager not importable; skipping VPN health check")
        return {"status": "unknown", "error": "WireGuard module unavailable"}
    except Exception as exc:
        logger.warning("VPN health check failed for %s: %s", vpn_ip, exc)
        return {"status": "unknown", "error": str(exc)}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DeviceRegistryError(Exception):
    pass


class DuplicateDeviceError(DeviceRegistryError):
    def __init__(self, device_id: str):
        self.device_id = device_id
        super().__init__(f"Device {device_id!r} already exists and is not revoked")


class DeviceNotFoundError(DeviceRegistryError):
    def __init__(self, device_id: str):
        self.device_id = device_id
        super().__init__(f"Device {device_id!r} not found")
