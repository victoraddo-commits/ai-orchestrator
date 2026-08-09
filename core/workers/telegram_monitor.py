"""Telegram monitor worker — periodic status digests to the operator.

Per operator directive 2026-08-09: "need 1 worker to monitor all task and
things that are happening to me via telegram at all times."

This module provides a TelegramMonitor class that runs on its own thread,
polling Kai's internal state every 60 seconds and sending a concise status
digest to the operator's Telegram chat whenever something meaningful changes.

Design principles:
- Concise: one message per digest, not a firehose
- Signal over noise: only sends when something changed or needs attention
- Non-blocking: never blocks the scheduler cycle, runs on its own daemon thread
- State-change gating: reuses telegram_bridge.detect_state_changes for builds
"""

import threading
import time
from datetime import datetime, timezone
from typing import Optional

import core.telegram_bridge as telegram_bridge
import core.build_manager as build_manager
import core.ai.ai_router as ai_router
import core.ai.provider_health as provider_health
from core.logger import info as _log


# --- Configuration ---

# How often the monitor wakes up to check state (seconds).
POLL_INTERVAL = 60

# Minimum interval between digest messages even if nothing changed (seconds).
# This is the "heartbeat" — a quiet "all clear" sent at most this often.
HEARTBEAT_INTERVAL = 3600  # 1 hour

# How many builds to list in the status digest.
MAX_BUILDS_IN_DIGEST = 5

# Characters to truncate build names at in the digest.
BUILD_NAME_MAX = 30


# --- Status labels ---

_STATUS_EMOJI = {
    "COMPLETED": "✅",
    "FAILED": "❌",
    "ROLLED_BACK": "↩️",
    "GENERATING": "⚙️",
    "CODE_REVIEW": "🔍",
    "TESTING": "🧪",
    "SECURITY_REVIEW": "🛡️",
    "WAITING_FOR_USER_INPUT": "❓",
    "WAITING_FOR_ARCHITECTURE_APPROVAL": "📋",
    "WAITING_FOR_DEPLOY_APPROVAL": "🚀",
    "ARCHITECTURE_APPROVED": "👍",
    "DEPLOYING": "📦",
    "VERIFIED": "✔️",
    "PLANNING": "🧠",
    "REQUESTED": "📥",
}

_HEALTH_EMOJI = {
    "healthy": "🟢",
    "busy": "🔵",
    "idle": "⚪",
    "degraded": "🟡",
    "error": "🔴",
    "quota_exceeded": "⛔",
    "circuit_open": "🔌",
    "latency_degraded": "🐌",
    "unknown": "❓",
    "not_configured": "⬜",
}


# --- Monitor ---

class TelegramMonitor:
    """Background thread that sends periodic status digests to Telegram.

    Usage:
        monitor = TelegramMonitor()
        monitor.start()
        # ... runs in background ...
        monitor.stop()
    """

    def __init__(self, interval: int = POLL_INTERVAL, chat_id: Optional[str] = None):
        self._interval = interval
        self._chat_id = chat_id or telegram_bridge.ALLOWED_CHAT_ID
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stop_event = threading.Event()

        # Track what we've already reported to avoid repeats
        self._last_build_states: dict[str, str] = {}  # build_id -> status
        self._last_digest_at: float = 0.0
        self._last_heartbeat_at: float = 0.0

    # --- Lifecycle ---

    def start(self):
        """Start the monitor in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="telegram-monitor",
            daemon=True,
        )
        self._thread.start()
        _log("Telegram monitor started")

    def stop(self):
        """Stop the monitor and wait for the thread to exit."""
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        _log("Telegram monitor stopped")

    # --- Main loop ---

    def _run(self):
        """Poll loop — wakes every POLL_INTERVAL seconds."""
        # Don't fire immediately on startup — wait one interval so the first
        # cycle has had time to do actual work.
        time.sleep(self._interval)

        while self._running:
            try:
                self._poll()
            except Exception as error:
                _log(f"telegram monitor poll error: {type(error).__name__}: {error}")

            # Sleep in 1s increments so stop() responds quickly
            deadline = time.time() + self._interval
            while time.time() < deadline and self._running:
                time.sleep(min(1.0, deadline - time.time()))

    def _poll(self):
        """One poll cycle: gather state, compare to last known, send if changed."""
        now = time.time()

        # Gather current state
        builds = self._safe_load_builds()
        dashboard = self._safe_dashboard()
        pool_status = self._safe_pool_status()

        # Detect build state changes
        changed_builds = self._detect_changed_builds(builds)
        has_changes = len(changed_builds) > 0

        # Determine if we should send
        send_heartbeat = (now - self._last_heartbeat_at) >= HEARTBEAT_INTERVAL
        should_send = has_changes or send_heartbeat

        if not should_send:
            return

        # Build digest message
        message = self._build_digest(builds, changed_builds, dashboard, pool_status, send_heartbeat)

        try:
            telegram_bridge.send_message(message, chat_id=self._chat_id)
            self._last_digest_at = now
            if send_heartbeat:
                self._last_heartbeat_at = now
            _log("telegram monitor: digest sent")
        except Exception as error:
            _log(f"telegram monitor send failed: {type(error).__name__}")

    # --- State gathering ---

    def _safe_load_builds(self) -> list:
        try:
            return build_manager.load_builds() or []
        except Exception:
            return []

    def _safe_dashboard(self) -> dict:
        try:
            return ai_router.get_provider_dashboard()
        except Exception:
            return {}

    def _safe_pool_status(self) -> dict:
        try:
            from core.workers.deepseek_pool import get_pool
            pool = get_pool()
            return pool.status()
        except Exception:
            return {}

    # --- Change detection ---

    def _detect_changed_builds(self, builds: list) -> list[dict]:
        """Return builds whose status has changed since last poll."""
        current = {}
        for b in builds:
            bid = b.get("id", "")
            if bid:
                current[bid] = b.get("status", "unknown")

        changed = []
        for bid, status in current.items():
            prev = self._last_build_states.get(bid)
            if prev is not None and prev != status:
                changed.append({"id": bid, "name": self._build_display_name(bid, builds), "status": status, "previous": prev})
            elif prev is None:
                # New build — report it
                changed.append({"id": bid, "name": self._build_display_name(bid, builds), "status": status, "previous": None})

        self._last_build_states = current
        return changed

    def _build_display_name(self, build_id: str, builds: list) -> str:
        for b in builds:
            if b.get("id") == build_id:
                name = b.get("name", build_id)
                return name[:BUILD_NAME_MAX]
        return build_id[:8]

    # --- Digest formatting ---

    def _build_digest(self, builds: list, changed: list, dashboard: dict, pool_status: dict, is_heartbeat: bool) -> str:
        """Format a concise status digest for Telegram."""
        now = datetime.now(timezone.utc)

        # Header
        if is_heartbeat and not changed:
            header = f"🫀 Kai Heartbeat — {now.strftime('%H:%M UTC')}"
        elif changed:
            header = f"📊 Kai Update — {now.strftime('%H:%M UTC')} — {len(changed)} change(s)"
        else:
            header = f"📊 Kai Status — {now.strftime('%H:%M UTC')}"

        lines = [header, ""]

        # --- Build changes (most important) ---
        if changed:
            lines.append("📋 *Build Changes:*")
            for c in changed[:MAX_BUILDS_IN_DIGEST]:
                emoji = _STATUS_EMOJI.get(c["status"], "•")
                prev_str = f" (was: {c.get('previous', '?')})" if c.get("previous") else " (new)"
                lines.append(f"  {emoji} {c['name']} → {c['status']}{prev_str}")
            if len(changed) > MAX_BUILDS_IN_DIGEST:
                lines.append(f"  ... and {len(changed) - MAX_BUILDS_IN_DIGEST} more changes")
            lines.append("")

        # --- Active builds summary ---
        active = [b for b in builds if b.get("status") not in ("COMPLETED", "FAILED", "ROLLED_BACK")]
        if active:
            lines.append(f"⚙️ *Active Builds ({len(active)}):*")
            for b in active[:MAX_BUILDS_IN_DIGEST]:
                emoji = _STATUS_EMOJI.get(b.get("status", ""), "•")
                name = (b.get("name") or "?")[:BUILD_NAME_MAX]
                status = b.get("status", "?")
                lines.append(f"  {emoji} {name}: {status}")
            if len(active) > MAX_BUILDS_IN_DIGEST:
                lines.append(f"  ... and {len(active) - MAX_BUILDS_IN_DIGEST} more")
            lines.append("")

        # --- Provider health (compact) ---
        if dashboard:
            interesting = {k: v for k, v in dashboard.items() if v.get("health") not in ("healthy", "idle", "not_configured", "unknown")}
            if interesting:
                lines.append("🔬 *Provider Health:*")
                for name, info in sorted(interesting.items()):
                    emoji = _HEALTH_EMOJI.get(info.get("health", ""), "❓")
                    lines.append(f"  {emoji} {name}: {info.get('health', '?')}")
                lines.append("")

        # --- Worker pool ---
        if pool_status.get("running"):
            lines.append(
                f"👷 *DeepSeek Workers:* "
                f"{pool_status.get('active', 0)}/{pool_status.get('workers', '?')} active, "
                f"{pool_status.get('queued', 0)} queued, "
                f"✅{pool_status.get('completed', 0)} ❌{pool_status.get('failed', 0)}"
            )
            lines.append("")

        # Footer
        lines.append("_Kai AI Orchestrator — DeepSeek-powered_")

        return "\n".join(lines)
