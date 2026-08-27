"""Scheduled tasks for Free Model Manager.

Handles:
- Periodic model discovery (every 6 hours)
- Periodic health checks (every 15 minutes)
- Recovery checks
- Integration with Kai orchestrator
"""

import logging
import threading
import time
import traceback
from datetime import datetime
from typing import Optional, Callable

from . import DISCOVERY_INTERVAL_SECONDS, HEALTH_CHECK_INTERVAL_SECONDS
from .models import db
from .discovery import discover_models, verify_model_free, test_omniroute_endpoint
from .validator import quick_health_check, run_full_validation
from .scorer import score_model, get_pool_ranking
from .router import (
    automatic_failover, check_recovery_and_restore,
    get_current_primary, get_available_models, update_kai_config
)
from .notifier import (
    send_notification, notify_pool_critical,
    notify_discovery_complete, notify_model_became_paid
)


logger = logging.getLogger("free_model_manager")


class FreeModelScheduler:
    """Scheduler for periodic Free Model Manager tasks."""

    def __init__(self):
        self._stop_event = threading.Event()
        self._discovery_thread: Optional[threading.Thread] = None
        self._health_thread: Optional[threading.Thread] = None
        self._notify_callback: Optional[Callable] = None

    def set_notify_callback(self, callback: Callable):
        """Set callback for notifications."""
        self._notify_callback = callback

    def _notify(self, event: dict):
        """Send notification via callback or direct."""
        if self._notify_callback:
            self._notify_callback(event)
        else:
            send_notification(event)

    def run_discovery_cycle(self) -> dict:
        """Run a complete discovery cycle.

        Returns: dict with results
        """
        logger.info("Starting discovery cycle")
        start_time = time.time()

        results = {
            "success": False,
            "discovered": 0,
            "verified_free": 0,
            "coding_qualified": 0,
            "duration_seconds": 0,
            "errors": []
        }

        try:
            # Check OmniRoute availability
            if not test_omniroute_endpoint():
                logger.warning("OmniRoute not reachable, skipping discovery")
                results["errors"].append("OmniRoute not reachable")
                return results

            # Run discovery
            verified_models = discover_models(verify_pricing=True)
            results["discovered"] = len(verified_models)

            # Count verified free
            all_models = db.get_all_models()
            results["verified_free"] = sum(1 for m in all_models if m.get("is_free") == 1)
            results["coding_qualified"] = sum(1 for m in all_models if (m.get("coding_score") or 0) > 5.0)

            # Notify completion
            notify_discovery_complete(
                results["discovered"],
                results["verified_free"],
                results["coding_qualified"]
            )

            # Check for new coding-qualified models
            new_candidates = [m for m in all_models
                            if m.get("is_free") == 1
                            and (m.get("coding_score") or 0) > 5.0
                            and m.get("status") not in ("AVAILABLE", "ACTIVE", "DEGRADED")]

            if new_candidates:
                logger.info(f"Found {len(new_candidates)} new coding-qualified models")

                # Sort by overall score
                new_candidates.sort(key=lambda m: m.get("overall_score", 0), reverse=True)

                # Auto-benchmark top candidate if none are available
                available = get_available_models()
                if not available and new_candidates:
                    top_candidate = new_candidates[0]
                    logger.info(f"Running full benchmark on {top_candidate['model_id']}")
                    validation_results = run_full_validation(top_candidate["model_id"])
                    score_model(top_candidate["model_id"], validation_results)

                    # If it still qualifies, promote it
                    updated = db.get_model(top_candidate["model_id"])
                    if updated.get("status") in ("AVAILABLE", "ACTIVE"):
                        update_kai_config(top_candidate["model_id"])
                        logger.info(f"Auto-promoted {top_candidate['model_id']} to primary")

            results["success"] = True

        except Exception as e:
            logger.error(f"Discovery cycle failed: {e}\n{traceback.format_exc()}")
            results["errors"].append(str(e))

        results["duration_seconds"] = time.time() - start_time
        logger.info(f"Discovery cycle complete in {results['duration_seconds']:.1f}s")

        return results

    def run_health_check_cycle(self) -> dict:
        """Run health check on all active/available models.

        Returns: dict with results
        """
        logger.info("Starting health check cycle")
        start_time = time.time()

        results = {
            "models_checked": 0,
            "healthy": 0,
            "unhealthy": 0,
            "failed": [],
            "recovered": [],
            "duration_seconds": 0
        }

        try:
            # Check primary and available models
            models_to_check = []

            primary = get_current_primary()
            if primary:
                models_to_check.append(primary)

            available = get_available_models()
            models_to_check.extend([m for m in available if m["model_id"] != primary.get("model_id") if primary])

            # Deduplicate
            seen = set()
            unique_models = []
            for m in models_to_check:
                if m["model_id"] not in seen:
                    seen.add(m["model_id"])
                    unique_models.append(m)

            results["models_checked"] = len(unique_models)

            for model in unique_models:
                model_id = model["model_id"]

                healthy, error, latency = quick_health_check(model_id)

                if healthy:
                    results["healthy"] += 1
                    # Record successful health check
                    db.record_request(model_id, True, latency)
                else:
                    results["unhealthy"] += 1
                    results["failed"].append({"model": model_id, "error": error})

                    # Handle the failure
                    from .router import handle_request_failure
                    handle_request_failure(model_id, "health_check", error, latency, self._notify)

            # Check for recovered models
            recovered = check_recovery_and_restore(self._notify)
            results["recovered"] = recovered

            # Check if pool is critical
            if not available and not primary:
                notify_pool_critical()

        except Exception as e:
            logger.error(f"Health check cycle failed: {e}\n{traceback.format_exc()}")

        results["duration_seconds"] = time.time() - start_time
        logger.info(f"Health check cycle complete: {results['healthy']}/{results['models_checked']} healthy")

        return results

    def run_pricing_verification_cycle(self) -> dict:
        """Re-verify pricing for all free models to ensure they haven't changed.

        Returns: dict with results
        """
        logger.info("Starting pricing verification cycle")

        results = {
            "models_checked": 0,
            "still_free": 0,
            "became_paid": [],
            "errors": []
        }

        try:
            free_models = db.get_models_by_status("FREE_VERIFIED")
            results["models_checked"] = len(free_models)

            for model in free_models:
                model_id = model["model_id"]

                still_free = verify_model_free(model_id)

                if still_free:
                    results["still_free"] += 1
                else:
                    results["became_paid"].append(model_id)
                    logger.warning(f"Model {model_id} is no longer free!")

                    # Get replacement
                    available = get_available_models()
                    replacement = available[0]["model_id"] if available else None

                    # Notify
                    self._notify({
                        "title": "🔴 FREE MODEL REMOVED",
                        "body": f"Model: {model_id}\nReason: OpenRouter endpoint is no longer free\nAction: removed from free pool\nReplacement: {replacement or 'none'}",
                        "severity": "warn"
                    })

                    # Record
                    db.update_status(model_id, "PAID", "Pricing changed from $0 to paid")

                    # If this was the primary, failover
                    primary = get_current_primary()
                    if primary and primary["model_id"] == model_id:
                        logger.warning(f"Primary model {model_id} became paid, triggering failover")
                        automatic_failover(self._notify)

        except Exception as e:
            logger.error(f"Pricing verification failed: {e}")
            results["errors"].append(str(e))

        return results

    def discovery_worker(self):
        """Background worker for periodic discovery."""
        while not self._stop_event.is_set():
            try:
                self.run_discovery_cycle()
            except Exception as e:
                logger.error(f"Discovery worker error: {e}\n{traceback.format_exc()}")

            # Wait for next cycle or stop signal
            self._stop_event.wait(DISCOVERY_INTERVAL_SECONDS)

    def health_check_worker(self):
        """Background worker for periodic health checks."""
        while not self._stop_event.is_set():
            try:
                self.run_health_check_cycle()

                # Run pricing verification less frequently (every 6 hours)
                # For now, run it with every 4th health check
                self._pricing_check_counter = getattr(self, '_pricing_check_counter', 0) + 1
                if self._pricing_check_counter >= 24:  # Every ~6 hours at 15min intervals
                    self.run_pricing_verification_cycle()
                    self._pricing_check_counter = 0

            except Exception as e:
                logger.error(f"Health check worker error: {e}\n{traceback.format_exc()}")

            # Wait for next cycle or stop signal
            self._stop_event.wait(HEALTH_CHECK_INTERVAL_SECONDS)

    def start(self):
        """Start the scheduler."""
        logger.info("Starting Free Model Manager scheduler")

        self._stop_event.clear()

        self._discovery_thread = threading.Thread(
            target=self.discovery_worker,
            name="free-model-discovery",
            daemon=True
        )
        self._discovery_thread.start()

        self._health_thread = threading.Thread(
            target=self.health_check_worker,
            name="free-model-health",
            daemon=True
        )
        self._health_thread.start()

        logger.info("Scheduler started (discovery: 6h, health checks: 15min)")

    def stop(self):
        """Stop the scheduler."""
        logger.info("Stopping Free Model Manager scheduler")
        self._stop_event.set()

        if self._discovery_thread:
            self._discovery_thread.join(timeout=10)

        if self._health_thread:
            self._health_thread.join(timeout=10)

        logger.info("Scheduler stopped")

    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return (
            self._discovery_thread is not None
            and self._discovery_thread.is_alive()
        ) or (
            self._health_thread is not None
            and self._health_thread.is_alive()
        )


# Global scheduler instance
scheduler = FreeModelScheduler()
