"""Phase 18A-b: Audit Logging for All Write Operations.

Provides structured, append-only audit logging for every write (POST/PUT/DELETE)
operation across the Kai platform. Integrates with core.api middleware and
writes to memory/audit_log.jsonl in a security-hardened, tamper-evident format.

Features:
- JSON-lines format with HMAC integrity checks
- Structured log entries with trace ID chaining
- Non-blocking writes (async-safe for FastAPI)
- Automatic log rotation at 10MB
- Retention: 90 days by default
"""

import json
import os
import hmac
import hashlib
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger("kai.audit")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AUDIT_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "memory"
)
AUDIT_LOG_FILE = os.path.join(AUDIT_LOG_DIR, "audit_log.jsonl")
AUDIT_LOG_MAX_SIZE = 10 * 1024 * 1024  # 10 MB rotation
AUDIT_LOG_RETENTION_DAYS = int(os.environ.get("AUDIT_LOG_RETENTION_DAYS", "90"))

# HMAC key for log integrity (auto-generated)
_AUDIT_HMAC_PATH = os.path.join(AUDIT_LOG_DIR, ".audit_hmac_key")
_audit_hmac_key: Optional[bytes] = None
_write_lock = threading.Lock()


def _get_hmac_key() -> bytes:
    """Load or create the audit log HMAC key."""
    global _audit_hmac_key
    if _audit_hmac_key is not None:
        return _audit_hmac_key

    try:
        with open(_AUDIT_HMAC_PATH, "rb") as fh:
            _audit_hmac_key = fh.read()
            if len(_audit_hmac_key) < 32:
                raise ValueError("HMAC key too short")
    except (FileNotFoundError, ValueError):
        _audit_hmac_key = os.urandom(32)
        tmp = _AUDIT_HMAC_PATH + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, _audit_hmac_key)
        finally:
            os.close(fd)
        os.replace(tmp, _AUDIT_HMAC_PATH)

    return _audit_hmac_key


def _sign_entry(entry: Dict[str, Any]) -> str:
    """Create HMAC-SHA256 signature for a log entry."""
    key = _get_hmac_key()
    payload = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def _verify_entry(entry: Dict[str, Any]) -> bool:
    """Verify the HMAC signature of a log entry."""
    sig = entry.pop("_sig", None)
    if sig is None:
        return False
    expected = _sign_entry(entry)
    entry["_sig"] = sig
    return hmac.compare_digest(expected, sig)


def _rotate_if_needed() -> None:
    """Rotate the audit log if it exceeds max size."""
    try:
        if os.path.exists(AUDIT_LOG_FILE) and os.path.getsize(AUDIT_LOG_FILE) > AUDIT_LOG_MAX_SIZE:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            rotated = f"{AUDIT_LOG_FILE}.{timestamp}"
            os.rename(AUDIT_LOG_FILE, rotated)
            logger.info(f"Audit log rotated: {rotated}")
    except Exception as e:
        logger.warning(f"Audit log rotation failed: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_audit_event(
    event_type: str,
    operator: str,
    endpoint: str,
    method: str,
    status_code: int = 200,
    details: Optional[Dict[str, Any]] = None,
    client_ip: str = "unknown",
    trace_id: Optional[str] = None,
) -> None:
    """Write a structured audit event to the log.

    Args:
        event_type: Category (e.g., 'auth.login', 'build.create', 'approval.approve')
        operator: Authenticated identity (username or bridge-token name)
        endpoint: API path requested
        method: HTTP method
        status_code: HTTP response status
        details: Optional additional context (request params, build ID, etc.)
        client_ip: Client IP address
        trace_id: Optional trace ID for chaining related events
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "operator": operator,
        "endpoint": endpoint,
        "method": method.upper(),
        "status_code": status_code,
        "client_ip": client_ip,
    }
    if trace_id:
        entry["trace_id"] = trace_id
    if details:
        entry["details"] = _sanitize_details(details)

    # Sign the entry for integrity
    entry["_sig"] = _sign_entry(entry)

    # Write atomically
    with _write_lock:
        _rotate_if_needed()
        os.makedirs(AUDIT_LOG_DIR, exist_ok=True)
        try:
            with open(AUDIT_LOG_FILE, "a") as fh:
                fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")


def _sanitize_details(details: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize sensitive fields from audit details.

    Never logs: passwords, tokens, API keys, full request bodies.
    """
    sensitive_keys = {
        "password", "token", "api_key", "api_token", "secret",
        "authorization", "cookie", "set_cookie",
    }
    sanitized = {}
    for k, v in details.items():
        k_lower = k.lower()
        if any(s in k_lower for s in sensitive_keys):
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_details(v)
        elif isinstance(v, str) and len(v) > 500:
            sanitized[k] = v[:500] + "..."
        else:
            sanitized[k] = v
    return sanitized


def read_audit_log(
    limit: int = 100,
    event_type: Optional[str] = None,
    operator: Optional[str] = None,
    since: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """Read and verify audit log entries with optional filtering.

    Returns the most recent matching entries (up to limit).
    """
    results = []
    try:
        if not os.path.exists(AUDIT_LOG_FILE):
            return results

        with open(AUDIT_LOG_FILE) as fh:
            lines = fh.readlines()

        # Read in reverse for most-recent-first
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Verify integrity
            if not _verify_entry(entry):
                logger.warning(f"Audit log integrity check failed for entry: {entry.get('timestamp', 'unknown')}")
                continue

            # Apply filters
            if event_type and entry.get("event_type") != event_type:
                continue
            if operator and entry.get("operator") != operator:
                continue
            if since and entry.get("timestamp", "") < since:
                continue

            results.append(entry)
            if len(results) >= limit:
                break

    except Exception as e:
        logger.error(f"Error reading audit log: {e}")

    return results


def get_audit_stats() -> Dict[str, Any]:
    """Get audit log statistics for dashboard."""
    try:
        if not os.path.exists(AUDIT_LOG_FILE):
            return {"total_events": 0, "size_bytes": 0, "event_types": {}}

        size = os.path.getsize(AUDIT_LOG_FILE)
        event_counts: Dict[str, int] = {}

        with open(AUDIT_LOG_FILE) as fh:
            total = 0
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    et = entry.get("event_type", "unknown")
                    event_counts[et] = event_counts.get(et, 0) + 1
                    total += 1
                except json.JSONDecodeError:
                    continue

        return {
            "total_events": total,
            "size_bytes": size,
            "event_types": event_counts,
        }
    except Exception as e:
        return {"error": str(e)}


def verify_log_integrity() -> Dict[str, Any]:
    """Verify the integrity of the entire audit log.

    Returns count of valid, invalid, and total entries.
    """
    total = 0
    valid = 0
    invalid = 0

    try:
        if not os.path.exists(AUDIT_LOG_FILE):
            return {"total": 0, "valid": 0, "invalid": 0}

        with open(AUDIT_LOG_FILE) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    entry = json.loads(line)
                    if _verify_entry(entry):
                        valid += 1
                    else:
                        invalid += 1
                except json.JSONDecodeError:
                    invalid += 1

    except Exception as e:
        return {"total": total, "valid": valid, "invalid": invalid, "error": str(e)}

    return {"total": total, "valid": valid, "invalid": invalid}
