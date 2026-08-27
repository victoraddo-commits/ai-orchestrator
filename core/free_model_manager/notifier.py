"""Telegram notifications via kai-notify hub.

Integrates with the existing Kai notification system to send alerts about:
- Model activation/promotion
- Failover events
- Model recovery
- New model discovery
- Model becoming paid
- Critical pool failures
"""

import json
import requests
from datetime import datetime
from typing import Optional, Callable

from . import NOTIFY_URL, NOTIFY_TOKEN


class NotifyError(Exception):
    """Notification error."""
    pass


def get_notify_headers() -> dict:
    """Get headers for kai-notify API."""
    return {
        "Authorization": f"Bearer {NOTIFY_TOKEN}",
        "Content-Type": "application/json",
    }


def send_notification(event: dict) -> dict:
    """Send notification via kai-notify hub.

    Args:
        event: dict with keys:
            - title: str (required)
            - body: str (optional)
            - severity: "info" | "warn" | "critical"
            - dedupe_key: str (optional, for deduplication)

    Returns: dict with notification result
    """
    if not NOTIFY_TOKEN:
        print(f"[free-model-manager] Notify: {event.get('title', 'No title')} (no token configured)")
        return {"status": "skipped_no_token"}

    try:
        response = requests.post(
            f"{NOTIFY_URL}/notify",
            headers=get_notify_headers(),
            json=event,
            timeout=10
        )

        if response.ok:
            result = response.json()
            return result
        else:
            print(f"[free-model-manager] Notify failed: {response.status_code} {response.text}")
            return {"status": "failed", "error": response.text}

    except requests.RequestException as e:
        print(f"[free-model-manager] Notify error: {e}")
        return {"status": "error", "error": str(e)}


def notify_model_activated(model_id: str, provider: str, coding_score: float,
                          overall_score: float, reason: str = "promoted to primary"):
    """Notify when a free model is activated."""
    return send_notification({
        "title": "FREE MODEL ACTIVATED",
        "body": f"Model: {model_id}\nProvider: {provider}\nCoding Score: {coding_score}/10\nOverall Score: {overall_score}/10\nReason: {reason}",
        "severity": "info",
        "dedupe_key": f"activated_{model_id}_{datetime.utcnow().date()}"
    })


def notify_failover(previous_model: str, problem: str, new_model: str,
                   provider: str):
    """Notify when automatic failover occurs."""
    return send_notification({
        "title": "⚠️ KAI FREE MODEL FAILOVER",
        "body": f"Previous: {previous_model}\nProblem: {problem}\nNew Model: {new_model}\nProvider: {provider}",
        "severity": "warn",
        "dedupe_key": f"failover_{new_model}_{datetime.utcnow().date()}"
    })


def notify_model_recovered(model_id: str):
    """Notify when a model recovers."""
    return send_notification({
        "title": "🟢 MODEL RECOVERED",
        "body": f"Model: {model_id}\nStatus: available again",
        "severity": "info",
        "dedupe_key": f"recovery_{model_id}_{datetime.utcnow().date()}"
    })


def notify_new_model_discovered(model_id: str, coding_score: float, context: int):
    """Notify when a new free coding model is discovered."""
    return send_notification({
        "title": "🔎 NEW FREE CODING MODEL",
        "body": f"Model: {model_id}\nCoding Score: {coding_score}/10\nContext: {context:,}\nFree: YES\nInference Test: PASS",
        "severity": "info",
        "dedupe_key": f"discovered_{model_id}"
    })


def notify_model_upgrade(old_model: str, new_model: str,
                        old_coding: float, new_coding: float,
                        old_overall: float, new_overall: float,
                        reason: str):
    """Notify when a better model is found and promoted."""
    return send_notification({
        "title": "🚀 FREE CODING MODEL UPGRADE",
        "body": f"Previous: {old_model}\nNew: {new_model}\n\nCoding:\n{old_coding:.1f} → {new_coding:.1f}\n\nOverall:\n{old_overall:.1f} → {new_overall:.1f}\n\nReason: {reason}",
        "severity": "info",
        "dedupe_key": f"upgrade_{new_model}_{datetime.utcnow().date()}"
    })


def notify_model_became_paid(model_id: str, replacement_model: Optional[str] = None):
    """Notify when a free model becomes paid."""
    body = f"Model: {model_id}\nReason: OpenRouter endpoint is no longer free\nAction: removed from free pool"
    if replacement_model:
        body += f"\nReplacement: {replacement_model}"

    return send_notification({
        "title": "🔴 FREE MODEL REMOVED",
        "body": body,
        "severity": "warn",
        "dedupe_key": f"paid_{model_id}_{datetime.utcnow().date()}"
    })


def notify_pool_critical(last_working_model: Optional[str] = None):
    """Notify when all models in the pool are failing."""
    body = "All verified free coding models are currently failing.\n\nAutomatic recovery/discovery is running."
    if last_working_model:
        body += f"\n\nLast working model:\n{last_working_model}"

    return send_notification({
        "title": "🚨 KAI FREE MODEL POOL CRITICAL",
        "body": body,
        "severity": "critical",
        "dedupe_key": f"pool_critical_{datetime.utcnow().date()}"
    })


def notify_discovery_complete(discovered: int, verified_free: int, coding_qualified: int):
    """Notify when a discovery cycle completes."""
    return send_notification({
        "title": "🔍 MODEL DISCOVERY COMPLETE",
        "body": f"Discovered: {discovered} coding models\nVerified Free: {verified_free}\nCoding Qualified: {coding_qualified}",
        "severity": "info",
        "dedupe_key": f"discovery_{datetime.utcnow().date()}"
    })


def notify_benchmark_complete(model_id: str, coding_score: float,
                             passed_tests: int, total_tests: int):
    """Notify when a model benchmark completes."""
    return send_notification({
        "title": "📊 BENCHMARK COMPLETE",
        "body": f"Model: {model_id}\nCoding Score: {coding_score}/10\nTests: {passed_tests}/{total_tests} passed",
        "severity": "info",
        "dedupe_key": f"benchmark_{model_id}"
    })


def notify_circuit_breaker_opened(model_id: str, failures: int, last_error: str):
    """Notify when a circuit breaker opens for a model."""
    return send_notification({
        "title": f"⚡ CIRCUIT BREAKER: {model_id}",
        "body": f"Model: {model_id}\nFailures: {failures}\nLast Error: {last_error[:100]}\nWill retry after cooldown.",
        "severity": "warn",
        "dedupe_key": f"circuit_open_{model_id}_{datetime.utcnow().date()}"
    })


def test_telegram_connection() -> tuple[bool, str]:
    """Test Telegram notification capability.

    Returns: (success, message)
    """
    result = send_notification({
        "title": "🧪 FREE MODEL MANAGER TEST",
        "body": "Telegram integration is working!",
        "severity": "info",
        "dedupe_key": "test_message"
    })

    if result.get("status") == "sent":
        return True, "Telegram notification sent successfully"
    elif result.get("status") == "skipped_no_token":
        return False, "No notification token configured"
    else:
        return False, f"Notification failed: {result.get('error', 'Unknown error')}"
