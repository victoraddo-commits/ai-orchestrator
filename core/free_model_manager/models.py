"""Persistent model database using SQLite.

Stores all discovered models with their state, scores, reliability metrics, etc.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import DB_PATH, DATA_DIR

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)


class ModelDatabase:
    """Thread-safe SQLite database for model state."""

    def __init__(self, db_path: str = str(DB_PATH)):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS models (
                    model_id TEXT PRIMARY KEY,
                    provider TEXT,
                    display_name TEXT,
                    context_length INTEGER,
                    first_seen TEXT,
                    last_seen TEXT,
                    price_prompt REAL,
                    price_completion REAL,
                    is_free INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'DISCOVERED',
                    coding_score REAL,
                    overall_score REAL,
                    last_test TEXT,
                    last_success TEXT,
                    last_failure TEXT,
                    last_error TEXT,
                    requests INTEGER DEFAULT 0,
                    successful_requests INTEGER DEFAULT 0,
                    failed_requests INTEGER DEFAULT 0,
                    timeouts INTEGER DEFAULT 0,
                    rate_limits INTEGER DEFAULT 0,
                    empty_responses INTEGER DEFAULT 0,
                    invalid_responses INTEGER DEFAULT 0,
                    tool_call_failures INTEGER DEFAULT 0,
                    total_latency_ms REAL DEFAULT 0,
                    latencies TEXT DEFAULT '[]',
                    success_rate REAL DEFAULT 0,
                    p50_latency REAL DEFAULT 0,
                    p95_latency REAL DEFAULT 0,
                    failover_count INTEGER DEFAULT 0,
                    promotion_count INTEGER DEFAULT 0,
                    demotion_count INTEGER DEFAULT 0,
                    circuit_breaker_failures INTEGER DEFAULT 0,
                    circuit_breaker_window_start INTEGER DEFAULT 0,
                    is_circuit_open INTEGER DEFAULT 0,
                    cooldown_until INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS config_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    config_type TEXT,
                    config_data TEXT,
                    backup_path TEXT,
                    is_active INTEGER DEFAULT 0
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    event_type TEXT,
                    model_id TEXT,
                    details TEXT,
                    FOREIGN KEY (model_id) REFERENCES models(model_id)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_models_status ON models(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_models_is_free ON models(is_free)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)
            """)
            conn.commit()
            conn.close()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def upsert_model(self, model_id: str, **fields):
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                fields["updated_at"] = datetime.utcnow().isoformat()
                set_clause = ", ".join(f"{k}=:{k}" for k in fields)
                cursor.execute(f"""
                    INSERT INTO models (model_id, {', '.join(fields.keys())})
                    VALUES (:model_id, {', '.join(':' + k for k in fields.keys())})
                    ON CONFLICT(model_id) DO UPDATE SET {set_clause}
                """, {"model_id": model_id, **fields})
                conn.commit()

    def get_model(self, model_id: str) -> Optional[dict]:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM models WHERE model_id = ?", (model_id,))
                row = cursor.fetchone()
                return dict(row) if row else None

    def get_all_models(self) -> list[dict]:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM models ORDER BY overall_score DESC")
                return [dict(row) for row in cursor.fetchall()]

    def get_models_by_status(self, status: str) -> list[dict]:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM models WHERE status = ? ORDER BY overall_score DESC", (status,))
                return [dict(row) for row in cursor.fetchall()]

    def get_verified_free_models(self) -> list[dict]:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM models
                    WHERE is_free = 1 AND status IN ('AVAILABLE', 'ACTIVE', 'DEGRADED')
                    ORDER BY overall_score DESC
                """)
                return [dict(row) for row in cursor.fetchall()]

    def get_active_pool(self) -> list[dict]:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM models
                    WHERE status = 'ACTIVE'
                    ORDER BY overall_score DESC
                """)
                return [dict(row) for row in cursor.fetchall()]

    def update_status(self, model_id: str, status: str, error: Optional[str] = None):
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                fields = {"status": status, "last_test": datetime.utcnow().isoformat()}
                if error:
                    fields["last_error"] = error
                if status == "AVAILABLE":
                    fields["last_success"] = datetime.utcnow().isoformat()
                elif status in ("FAILING", "OFFLINE"):
                    fields["last_failure"] = datetime.utcnow().isoformat()
                set_clause = ", ".join(f"{k}=:{k}" for k in fields)
                cursor.execute(f"""
                    UPDATE models SET {set_clause} WHERE model_id = :model_id
                """, {**fields, "model_id": model_id})
                conn.commit()
                self.log_event(model_id, status, f"Status changed to {status}" + (f": {error}" if error else ""))

    def record_request(self, model_id: str, success: bool, latency_ms: float,
                       error: Optional[str] = None, is_timeout: bool = False,
                       is_rate_limit: bool = False, is_empty: bool = False,
                       is_invalid: bool = False, is_tool_failure: bool = False):
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT requests, successful_requests, failed_requests, timeouts, rate_limits, empty_responses, invalid_responses, tool_call_failures, total_latency_ms, latencies FROM models WHERE model_id = ?", (model_id,))
                row = cursor.fetchone()
                if not row:
                    return

                r = dict(row)
                requests = r["requests"] + 1
                successful = r["successful_requests"] + (1 if success else 0)
                failed = r["failed_requests"] + (1 if not success else 0)
                timeouts = r["timeouts"] + (1 if is_timeout else 0)
                rate_limits = r["rate_limits"] + (1 if is_rate_limit else 0)
                empty_responses = r["empty_responses"] + (1 if is_empty else 0)
                invalid_responses = r["invalid_responses"] + (1 if is_invalid else 0)
                tool_failures = r["tool_call_failures"] + (1 if is_tool_failure else 0)
                total_latency = r["total_latency_ms"] + latency_ms

                latencies = json.loads(r["latencies"] or "[]")
                latencies.append(latency_ms)
                if len(latencies) > 1000:
                    latencies = latencies[-1000:]

                success_rate = (successful / requests * 100) if requests > 0 else 0
                p50 = sorted(latencies)[int(len(latencies) * 0.5)] if latencies else 0
                p95 = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

                cursor.execute("""
                    UPDATE models SET
                        requests = ?, successful_requests = ?, failed_requests = ?,
                        timeouts = ?, rate_limits = ?, empty_responses = ?,
                        invalid_responses = ?, tool_call_failures = ?,
                        total_latency_ms = ?, latencies = ?,
                        success_rate = ?, p50_latency = ?, p95_latency = ?,
                        updated_at = ?
                    WHERE model_id = ?
                """, (requests, successful, failed, timeouts, rate_limits, empty_responses,
                      invalid_responses, tool_failures, total_latency, json.dumps(latencies),
                      success_rate, p50, p95, datetime.utcnow().isoformat(), model_id))
                conn.commit()

    def record_failover(self, from_model: str, to_model: str, reason: str):
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE models SET failover_count = failover_count + 1 WHERE model_id = ?", (from_model,))
                conn.commit()
                self.log_event(from_model, "FAILOVER", f"Failed over to {to_model}: {reason}")
                self.log_event(to_model, "FAILOVER_ACTIVE", f"Activated as failover from {from_model}")

    def record_promotion(self, model_id: str, old_score: float, new_score: float, reason: str):
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE models SET promotion_count = promotion_count + 1 WHERE model_id = ?", (model_id,))
                conn.commit()
                self.log_event(model_id, "PROMOTION", f"Promoted: {old_score:.2f} -> {new_score:.2f}. {reason}")

    def log_event(self, model_id: Optional[str], event_type: str, details: str):
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO events (event_type, model_id, details)
                    VALUES (?, ?, ?)
                """, (event_type, model_id, details))
                conn.commit()

    def get_events(self, model_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                if model_id:
                    cursor.execute("""
                        SELECT * FROM events
                        WHERE model_id = ?
                        ORDER BY timestamp DESC
                        LIMIT ?
                    """, (model_id, limit))
                else:
                    cursor.execute("SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (limit,))
                return [dict(row) for row in cursor.fetchall()]

    def get_stats(self) -> dict:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status, COUNT(*) as count FROM models GROUP BY status")
                status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}
                cursor.execute("SELECT COUNT(*) as total FROM models")
                total = cursor.fetchone()["total"]
                cursor.execute("SELECT COUNT(*) as verified FROM models WHERE is_free = 1")
                verified = cursor.fetchone()["verified"]
                cursor.execute("SELECT COUNT(*) as active FROM models WHERE status = 'ACTIVE'")
                active = cursor.fetchone()["active"]
                return {
                    "total_models": total,
                    "verified_free": verified,
                    "active": active,
                    "by_status": status_counts
                }

    def circuit_breaker_record_failure(self, model_id: str) -> tuple[bool, int]:
        """Record a circuit breaker failure. Returns (should_open, current_failures)."""
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT circuit_breaker_failures, circuit_breaker_window_start, is_circuit_open, cooldown_until
                    FROM models WHERE model_id = ?
                """, (model_id,))
                row = cursor.fetchone()
                if not row:
                    return False, 0

                r = dict(row)
                now_ms = int(time.time() * 1000)
                window_start = r["circuit_breaker_window_start"]
                failures = r["circuit_breaker_failures"]

                # Reset window if expired
                if now_ms - window_start > 300000:  # 5 minutes
                    window_start = now_ms
                    failures = 0

                failures += 1
                should_open = failures >= 3

                if should_open:
                    cooldown = now_ms + 900000  # 15 minutes
                    cursor.execute("""
                        UPDATE models SET
                            circuit_breaker_failures = ?,
                            circuit_breaker_window_start = ?,
                            is_circuit_open = 1,
                            cooldown_until = ?,
                            updated_at = ?
                        WHERE model_id = ?
                    """, (failures, window_start, cooldown, datetime.utcnow().isoformat(), model_id))
                else:
                    cursor.execute("""
                        UPDATE models SET
                            circuit_breaker_failures = ?,
                            circuit_breaker_window_start = ?,
                            updated_at = ?
                        WHERE model_id = ?
                    """, (failures, window_start, datetime.utcnow().isoformat(), model_id))
                conn.commit()
                return should_open, failures

    def circuit_breaker_check(self, model_id: str) -> tuple[bool, int]:
        """Check if circuit is open. Returns (is_open, cooldown_remaining_ms)."""
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT is_circuit_open, cooldown_until FROM models WHERE model_id = ?
                """, (model_id,))
                row = cursor.fetchone()
                if not row:
                    return False, 0

                r = dict(row)
                if not r["is_circuit_open"]:
                    return False, 0

                now_ms = int(time.time() * 1000)
                if now_ms >= r["cooldown_until"]:
                    # Cooldown expired, reset
                    cursor.execute("""
                        UPDATE models SET
                            is_circuit_open = 0,
                            circuit_breaker_failures = 0,
                            updated_at = ?
                        WHERE model_id = ?
                    """, (datetime.utcnow().isoformat(), model_id))
                    conn.commit()
                    return False, 0

                return True, r["cooldown_until"] - now_ms

    def save_config_snapshot(self, config_type: str, config_data: dict, backup_path: Optional[str] = None):
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                # Deactivate old snapshots of this type
                cursor.execute("UPDATE config_snapshots SET is_active = 0 WHERE config_type = ?", (config_type,))
                cursor.execute("""
                    INSERT INTO config_snapshots (config_type, config_data, backup_path, is_active)
                    VALUES (?, ?, ?, 1)
                """, (config_type, json.dumps(config_data), backup_path))
                conn.commit()
                return cursor.lastrowid

    def get_active_config(self, config_type: str) -> Optional[dict]:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM config_snapshots
                    WHERE config_type = ? AND is_active = 1
                    ORDER BY created_at DESC LIMIT 1
                """, (config_type,))
                row = cursor.fetchone()
                if row:
                    return {
                        "id": row["id"],
                        "created_at": row["created_at"],
                        "config_data": json.loads(row["config_data"]),
                        "backup_path": row["backup_path"]
                    }
                return None

    def get_config_snapshots(self, config_type: str, limit: int = 10) -> list[dict]:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM config_snapshots
                    WHERE config_type = ?
                    ORDER BY created_at DESC LIMIT ?
                """, (config_type, limit))
                return [dict(row) for row in cursor.fetchall()]


# Global database instance
db = ModelDatabase()
