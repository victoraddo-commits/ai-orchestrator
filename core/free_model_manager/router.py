"""Active model pool router with automatic failover and circuit breaker.

Manages the ranked pool of verified free coding models and handles:
- Automatic failover on errors, timeouts, rate limits
- Circuit breaker implementation
- Recovery with cooldown period
- Configuration backup and rollback
"""

import json
import os
import time
import requests
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional
from threading import Lock

from . import (
    OMNIROUTE_BASE_URL, OPENROUTER_API_KEY, BACKUP_DIR, MAX_BACKUPS,
    CIRCUIT_BREAKER_FAILURES, CIRCUIT_BREAKER_COOLDOWN_MS,
    MIN_IMPROVEMENT_THRESHOLD
)
from .models import db
from .scorer import should_promote, get_pool_ranking

_env_lock = Lock()


class RouterError(Exception):
    """Router operation error."""
    pass


def get_current_primary() -> Optional[dict]:
    """Get the current primary (ACTIVE) model."""
    active_models = db.get_active_pool()
    return active_models[0] if active_models else None


def get_available_models() -> list[dict]:
    """Get all available (non-circuit-open) models sorted by rank."""
    verified = db.get_verified_free_models()

    available = []
    for model in verified:
        # Skip circuit-broken models
        is_open, remaining = db.circuit_breaker_check(model["model_id"])
        if is_open:
            continue

        # Skip models with unknown/paid status
        if model.get("status") in ("PAID", "RETIRED", "REMOVED", "REJECTED", "UNKNOWN"):
            continue

        available.append(model)

    return sorted(available, key=lambda m: m.get("overall_score", 0), reverse=True)


def backup_current_config() -> str:
    """Backup the current OmniRoute/Kai configuration.

    Returns: path to backup file
    """
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"config_backup_{timestamp}.json"

    # Get current active config snapshot
    active_config = db.get_active_config("free_coding")

    config_data = {
        "timestamp": timestamp,
        "primary_model": None,
        "pool": get_pool_ranking(),
        "active_config_snapshot": active_config,
        "omnioute_url": OMNIROUTE_BASE_URL,
    }

    # Try to read current Kai config if it exists
    kai_config_path = Path(__file__).parent.parent.parent / "config" / "providers.yaml"
    if kai_config_path.exists():
        try:
            config_data["kai_config_path"] = str(kai_config_path)
            config_data["kai_config_mtime"] = kai_config_path.stat().st_mtime
        except Exception:
            pass

    tmp = backup_path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(config_data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(backup_path)
    db.save_config_snapshot("free_coding", config_data, str(backup_path))

    # Cleanup old backups
    cleanup_old_backups()

    return str(backup_path)


def cleanup_old_backups():
    """Remove old backups, keeping only MAX_BACKUPS most recent."""
    backups = sorted(BACKUP_DIR.glob("config_backup_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_backup in backups[MAX_BACKUPS:]:
        try:
            old_backup.unlink()
        except Exception:
            pass


def rollback_to_config(config_id: int = None) -> bool:
    """Rollback to a previous configuration snapshot.

    Args:
        config_id: Specific config ID to restore. If None, restore most recent.

    Returns: True if rollback successful
    """
    if config_id:
        snapshots = db.get_config_snapshots("free_coding", limit=100)
        target = next((s for s in snapshots if s["id"] == config_id), None)
    else:
        # Get most recent non-active snapshot
        snapshots = db.get_config_snapshots("free_coding", limit=10)
        target = next((s for s in snapshots if not s.get("is_active")), None)

    if not target:
        return False

    config_data = target.get("config_data", {})

    # Restore model status
    primary_model = config_data.get("primary_model")
    if primary_model:
        # Demote current primary
        current_primary = get_current_primary()
        if current_primary:
            db.update_status(current_primary["model_id"], "AVAILABLE")

        # Promote restored primary
        db.update_status(primary_model, "ACTIVE")

    return True


def update_kai_config(model_id: str, dry_run: bool = False) -> dict:
    """Update Kai's configuration to use a new free coding model.

    Args:
        model_id: The model to configure
        dry_run: If True, don't actually make changes

    Returns: dict with result details
    """
    result = {
        "model_id": model_id,
        "success": False,
        "backup_created": False,
        "config_updated": False,
        "health_check_passed": False,
        "error": None,
    }

    # Step 1: Backup current config
    if not dry_run:
        backup_path = backup_current_config()
        result["backup_created"] = True
        print(f"[free-model-manager] Backed up config to {backup_path}")

    # Step 2: Validate new model exists and is free
    model = db.get_model(model_id)
    if not model:
        result["error"] = f"Model {model_id} not found in database"
        return result

    if not model.get("is_free"):
        result["error"] = f"Model {model_id} is not verified free"
        return result

    # Step 3: Run health check
    from .validator import quick_health_check
    healthy, error, latency = quick_health_check(model_id)
    if not healthy:
        result["error"] = f"Health check failed: {error}"
        return result

    result["health_check_passed"] = True
    print(f"[free-model-manager] Health check passed for {model_id} (latency: {latency:.0f}ms)")

    if dry_run:
        result["success"] = True
        return result

    # Step 4: Update configuration
    # Update OmniRoute's free coding model config
    # We do this by setting environment variable and/or updating config files

    # First, check if we can reach OmniRoute
    try:
        response = requests.get(f"{OMNIROUTE_BASE_URL}/models", timeout=5)
        omni_available = response.ok
    except Exception:
        omni_available = False

    if omni_available:
        # Update Kai's environment/config
        # The actual implementation depends on how Kai reads its config
        # We'll update the .env file if needed
        kai_env_path = Path(__file__).parent.parent.parent / ".env"

        if kai_env_path.exists():
            with _env_lock:
                env_content = kai_env_path.read_text()

                # Check if FREE_CODING_MODEL is already set
                if "FREE_CODING_MODEL" in env_content:
                    # Update existing
                    lines = env_content.split("\n")
                    new_lines = []
                    for line in lines:
                        if line.startswith("FREE_CODING_MODEL="):
                            new_lines.append(f'FREE_CODING_MODEL={model_id}')
                        else:
                            new_lines.append(line)
                    env_content = "\n".join(new_lines)
                else:
                    # Add new
                    env_content += f"\nFREE_CODING_MODEL={model_id}\n"

                tmp = kai_env_path.with_suffix(f".tmp{os.getpid()}")
                tmp.write_text(env_content)
                tmp.chmod(0o600)
                tmp.replace(kai_env_path)

            result["config_updated"] = True
            print(f"[free-model-manager] Updated Kai .env with FREE_CODING_MODEL={model_id}")

    # Step 5: Update database status
    current_primary = get_current_primary()
    if current_primary:
        db.update_status(current_primary["model_id"], "AVAILABLE")
        db.record_promotion(
            model_id,
            current_primary.get("overall_score", 0),
            model.get("overall_score", 0),
            f"Promoted over {current_primary['model_id']}"
        )

    db.update_status(model_id, "ACTIVE")

    result["success"] = True
    return result


def automatic_failover(notify_callback=None) -> dict:
    """Execute automatic failover to next available model.

    Returns: dict with failover details
    """
    current_primary = get_current_primary()

    if current_primary:
        failed_model = current_primary["model_id"]
        # Record the failure
        is_open, failures = db.circuit_breaker_record_failure(failed_model)
        if is_open:
            db.update_status(failed_model, "DEGRADED", f"Circuit breaker opened after {failures} failures")
    else:
        failed_model = "none"

    # Get next available model
    available = get_available_models()

    if not available:
        # No available models - survival mode
        print("[free-model-manager] ⚠️ NO AVAILABLE MODELS - Survival mode activated")

        if notify_callback:
            notify_callback({
                "title": "KAI FREE MODEL POOL CRITICAL",
                "body": "All verified free coding models are currently unavailable.\nAutomatic recovery is running.",
                "severity": "critical",
                "dedupe_key": "pool_critical"
            })

        return {
            "success": False,
            "failed_model": failed_model,
            "new_model": None,
            "survival_mode": True,
            "message": "No models available - survival mode"
        }

    # Pick highest-ranked available model
    new_primary = available[0]
    new_model_id = new_primary["model_id"]

    print(f"[free-model-manager] Executing failover: {failed_model} -> {new_model_id}")

    # Update Kai config
    result = update_kai_config(new_model_id)

    if result["success"]:
        # Record failover
        if failed_model != "none":
            db.record_failover(failed_model, new_model_id, "Automatic failover")

        if notify_callback:
            notify_callback({
                "title": "KAI FREE MODEL FAILOVER",
                "body": f"Previous: {failed_model}\nProblem: {current_primary.get('last_error', 'Unknown')}\nNew Model: {new_model_id}\nProvider: {new_primary.get('provider', 'unknown')}",
                "severity": "warn",
                "dedupe_key": f"failover_{new_model_id}_{datetime.utcnow().date()}"
            })

    return {
        "success": result["success"],
        "failed_model": failed_model,
        "new_model": new_model_id,
        "survival_mode": False,
        "result": result
    }


def handle_request_failure(model_id: str, error_type: str, error_detail: str,
                          latency_ms: float, notify_callback=None) -> bool:
    """Handle a request failure for a model.

    Records the failure, checks circuit breaker, and triggers failover if needed.

    Returns: True if failover was triggered
    """
    # Record the failure
    is_timeout = error_type == "timeout"
    is_rate_limit = error_type == "rate_limit"
    is_empty = error_type == "empty"
    is_invalid = error_type == "invalid"

    db.record_request(
        model_id=model_id,
        success=False,
        latency_ms=latency_ms,
        error=error_detail,
        is_timeout=is_timeout,
        is_rate_limit=is_rate_limit,
        is_empty=is_empty,
        is_invalid=is_invalid
    )

    # Check circuit breaker
    should_open, failures = db.circuit_breaker_record_failure(model_id)

    if should_open:
        print(f"[free-model-manager] Circuit breaker opened for {model_id} after {failures} failures")
        db.update_status(model_id, "DEGRADED", f"Circuit breaker opened: {error_detail}")

        if notify_callback:
            notify_callback({
                "title": f"CIRCUIT BREAKER OPEN: {model_id}",
                "body": f"Model: {model_id}\nFailures: {failures}\nLast Error: {error_detail}\nWill retry after cooldown.",
                "severity": "warn",
                "dedupe_key": f"circuit_{model_id}_{datetime.utcnow().date()}"
            })

        # Trigger failover
        failover_result = automatic_failover(notify_callback)
        return True

    # Not circuit-broken yet, but still failing
    current_primary = get_current_primary()
    if current_primary and current_primary["model_id"] == model_id:
        # Primary is failing - check if we should failover anyway
        if is_rate_limit or is_timeout:
            # Rate limits and timeouts warrant immediate failover
            failover_result = automatic_failover(notify_callback)
            return True

    return False


def route_request(prompt: str, require_tool_use: bool = False,
                 notify_callback=None) -> tuple[str, str, float]:
    """Route a coding request to the best available free model.

    Returns: (response_text, model_id, latency_ms)

    Raises: RouterError if no models available
    """
    available = get_available_models()

    if not available:
        raise RouterError("No available free coding models")

    # Try models in order of rank
    for model in available:
        model_id = model["model_id"]

        # Try inference
        start_time = time.time()
        try:
            from .validator import run_inference
            success, response, latency = run_inference(model_id, prompt, timeout=90)

            if success:
                # Record success
                db.record_request(model_id, True, latency)

                # Check if this is a better model than current primary
                current_primary = get_current_primary()
                if current_primary and model.get("overall_score", 0) > current_primary.get("overall_score", 0):
                    should_promo, reason = should_promote(model, current_primary, MIN_IMPROVEMENT_THRESHOLD)
                    if should_promo:
                        print(f"[free-model-manager] {model_id} qualifies for promotion")
                        # Don't auto-promote from a single success, require more evidence

                return response, model_id, latency

            # Handle failure
            error_type = "api_error"
            if "429" in str(response):
                error_type = "rate_limit"
            elif not response:
                error_type = "empty"

            handle_request_failure(model_id, error_type, str(response), latency, notify_callback)

        except Exception as e:
            latency = (time.time() - start_time) * 1000
            handle_request_failure(model_id, "exception", str(e), latency, notify_callback)

    # All models failed
    raise RouterError("All free coding models failed")


def check_recovery_and_restore(notify_callback=None) -> list[str]:
    """Check for models that have recovered and restore them.

    Returns: list of models that recovered
    """
    recovered = []

    # Get degraded models
    degraded = db.get_models_by_status("DEGRADED")

    for model in degraded:
        model_id = model["model_id"]

        # Check if circuit is closed
        is_open, remaining = db.circuit_breaker_check(model_id)

        if not is_open:
            # Model recovered
            db.update_status(model_id, "AVAILABLE")

            # Run quick health check
            from .validator import quick_health_check
            healthy, _, _ = quick_health_check(model_id)

            if healthy:
                recovered.append(model_id)
                print(f"[free-model-manager] Model recovered: {model_id}")

                if notify_callback:
                    notify_callback({
                        "title": "MODEL RECOVERED",
                        "body": f"Model: {model_id}\nStatus: available again",
                        "severity": "info",
                        "dedupe_key": f"recovery_{model_id}_{datetime.utcnow().date()}"
                    })
            else:
                db.update_status(model_id, "FAILING", "Health check after recovery failed")

    return recovered


def get_pool_status() -> dict:
    """Get current pool status."""
    pool = get_pool_ranking()
    available = get_available_models()
    degraded = db.get_models_by_status("DEGRADED")
    stats = db.get_stats()

    return {
        "pool": pool,
        "available_count": len(available),
        "degraded_count": len(degraded),
        "total_discovered": stats["total_models"],
        "verified_free": stats["verified_free"],
        "primary": get_current_primary(),
    }
