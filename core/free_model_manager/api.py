"""REST API endpoints for Free Model Manager.

Provides:
- GET /health - health check
- GET /status - pool status
- GET /models - list all models
- GET /models/{id} - get model details
- GET /pool - current model pool
- POST /discover - trigger discovery
- POST /benchmark/{id} - run benchmark on model
- POST /promote/{id} - promote model
- POST /disable/{id} - disable model
- GET /events - event log
- GET /logs - recent logs
- GET /history - failover/promotion history

Telegram commands (via query params):
- /free status
- /free models
- /free active
- /free test
- /free discover
- /free benchmark
- /free failover
- /free logs
- /free history
"""

import json
import logging
import os
import threading
import traceback
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

from . import FREE_CODING_PORT, LOG_PATH
from .models import db
from .scorer import get_pool_ranking, score_model
from .router import get_pool_status, get_current_primary, get_available_models, automatic_failover
from .discovery import discover_models, test_omniroute_endpoint, get_omniroute_models
from .validator import run_full_validation, quick_health_check
from .notifier import send_notification, test_telegram_connection


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("free_model_manager")


class FreeModelAPI:
    """REST API handler for Free Model Manager."""

    def __init__(self):
        self.lock = threading.Lock()

    def handle_request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """Handle an API request."""
        with self.lock:
            try:
                # Health check
                if method == "GET" and path == "/health":
                    return self.health()

                # Status
                if method == "GET" and path == "/status":
                    return self.status()

                # Models
                if method == "GET" and path == "/models":
                    return self.list_models()

                if method == "GET" and path.startswith("/models/"):
                    model_id = path[8:]
                    return self.get_model(model_id)

                # Pool
                if method == "GET" and path == "/pool":
                    return self.get_pool()

                # Actions
                if method == "POST" and path == "/discover":
                    return self.trigger_discover()

                if method == "POST" and path.startswith("/benchmark/"):
                    model_id = path[11:]
                    return self.run_benchmark(model_id)

                if method == "POST" and path.startswith("/promote/"):
                    model_id = path[9:]
                    return self.promote_model(model_id)

                if method == "POST" and path.startswith("/disable/"):
                    model_id = path[9:]
                    return self.disable_model(model_id)

                if method == "POST" and path == "/failover":
                    return self.manual_failover()

                # Inference (used by Kai's free_coding provider)
                if method == "POST" and path == "/infer":
                    return self.run_inference(body)

                # Events
                if method == "GET" and path == "/events":
                    return self.get_events()

                if method == "GET" and path == "/logs":
                    return self.get_logs()

                if method == "GET" and path == "/history":
                    return self.get_history()

                # Telegram test
                if method == "GET" and path == "/test":
                    return self.test_system()

                return {"error": "not_found", "message": f"Endpoint {path} not found"}, 404

            except Exception as e:
                logger.error(f"API error: {e}\n{traceback.format_exc()}")
                return {"error": "internal_error", "message": str(e)}, 500

    def health(self) -> tuple[dict, int]:
        """Health check endpoint."""
        omniroute_ok = test_omniroute_endpoint()
        stats = db.get_stats()

        return {
            "status": "healthy" if omniroute_ok else "degraded",
            "omniroute": "ok" if omniroute_ok else "unreachable",
            "stats": stats,
            "timestamp": datetime.utcnow().isoformat()
        }, 200

    def status(self) -> tuple[dict, int]:
        """Get overall system status."""
        pool_status = get_pool_status()
        primary = get_current_primary()
        omniroute_ok = test_omniroute_endpoint()

        return {
            "system": "healthy" if omniroute_ok and pool_status["available_count"] > 0 else "degraded",
            "omniroute": "ok" if omniroute_ok else "unreachable",
            "pool": pool_status,
            "primary": primary,
            "timestamp": datetime.utcnow().isoformat()
        }, 200

    def list_models(self) -> tuple[dict, int]:
        """List all models."""
        models = db.get_all_models()

        # Convert JSON strings back to dicts
        for m in models:
            if m.get("metadata"):
                try:
                    m["metadata"] = json.loads(m["metadata"])
                except Exception:
                    pass
            if m.get("latencies"):
                try:
                    m["latencies"] = json.loads(m["latencies"])
                except Exception:
                    pass

        return {"models": models, "count": len(models)}, 200

    def get_model(self, model_id: str) -> tuple[dict, int]:
        """Get details for a specific model."""
        model = db.get_model(model_id)

        if not model:
            return {"error": "not_found", "message": f"Model {model_id} not found"}, 404

        # Parse JSON fields
        if model.get("metadata"):
            try:
                model["metadata"] = json.loads(model["metadata"])
            except Exception:
                pass

        return {"model": model}, 200

    def get_pool(self) -> tuple[dict, int]:
        """Get current model pool."""
        pool = get_pool_ranking()
        available = get_available_models()

        return {
            "pool": pool,
            "available_models": [m["model_id"] for m in available],
            "count": len(available)
        }, 200

    def trigger_discover(self) -> tuple[dict, int]:
        """Trigger model discovery."""
        logger.info("Manual discovery triggered")

        try:
            discovered = discover_models(verify_pricing=True)
            stats = db.get_stats()

            return {
                "success": True,
                "discovered": len(discovered),
                "stats": stats,
                "timestamp": datetime.utcnow().isoformat()
            }, 200
        except Exception as e:
            logger.error(f"Discovery failed: {e}")
            return {"success": False, "error": str(e)}, 500

    def run_benchmark(self, model_id: str) -> tuple[dict, int]:
        """Run full benchmark on a model."""
        logger.info(f"Manual benchmark triggered for {model_id}")

        model = db.get_model(model_id)
        if not model:
            return {"error": "not_found", "message": f"Model {model_id} not found"}, 404

        try:
            results = run_full_validation(model_id)

            # Score the model
            scores = score_model(model_id, results)

            # Send notification
            send_notification({
                "title": "📊 BENCHMARK COMPLETE",
                "body": f"Model: {model_id}\nCoding Score: {scores['coding_score']}/10\nTests: {results['passed_tests']}/{results['total_tests']} passed",
                "severity": "info"
            })

            return {
                "success": True,
                "model_id": model_id,
                "validation": results,
                "scores": scores,
                "timestamp": datetime.utcnow().isoformat()
            }, 200
        except Exception as e:
            logger.error(f"Benchmark failed: {e}")
            return {"success": False, "error": str(e)}, 500

    def promote_model(self, model_id: str) -> tuple[dict, int]:
        """Manually promote a model (after safety validation)."""
        logger.info(f"Manual promote triggered for {model_id}")

        model = db.get_model(model_id)
        if not model:
            return {"error": "not_found", "message": f"Model {model_id} not found"}, 404

        # Safety checks
        if not model.get("is_free"):
            return {"error": "model_not_free", "message": "Model is not verified free"}, 400

        if model.get("coding_score", 0) <= 5.0:
            return {"error": "insufficient_score", "message": "Coding score must be > 5.0 to promote"}, 400

        if model.get("status") in ("PAID", "RETIRED", "REMOVED", "REJECTED"):
            return {"error": "invalid_status", "message": f"Model status {model['status']} prevents promotion"}, 400

        # Run quick health check before promoting
        healthy, error, latency = quick_health_check(model_id)
        if not healthy:
            return {"error": "health_check_failed", "message": f"Health check failed: {error}"}, 400

        # Update Kai config
        from .router import update_kai_config
        result = update_kai_config(model_id)

        if result["success"]:
            send_notification({
                "title": "FREE MODEL ACTIVATED",
                "body": f"Model: {model_id}\nCoding Score: {model.get('coding_score', 0)}/10\nOverall Score: {model.get('overall_score', 0)}/10\nReason: manual promotion",
                "severity": "info"
            })

        return {
            "success": result["success"],
            "model_id": model_id,
            "result": result
        }, 200 if result["success"] else 500

    def disable_model(self, model_id: str) -> tuple[dict, int]:
        """Disable a model (remove from active pool)."""
        logger.info(f"Manual disable triggered for {model_id}")

        model = db.get_model(model_id)
        if not model:
            return {"error": "not_found", "message": f"Model {model_id} not found"}, 404

        db.update_status(model_id, "OFFLINE", "Manually disabled")

        return {
            "success": True,
            "model_id": model_id,
            "status": "OFFLINE"
        }, 200

    def manual_failover(self) -> tuple[dict, int]:
        """Manually trigger failover."""
        logger.info("Manual failover triggered")

        result = automatic_failover(send_notification)

        return result, 200

    def run_inference(self, body: dict) -> tuple[dict, int]:
        """Run inference against the active free coding model.

        Used by Kai's free_coding provider to route coding tasks through
        the verified free model pool.

        Body: {"prompt": str, "model": str (optional)}
        """
        if not body:
            return {"error": "bad_request", "message": "Missing request body"}, 400

        prompt = body.get("prompt")
        if not prompt:
            return {"error": "bad_request", "message": "Missing 'prompt' field"}, 400

        # Get model from body or use current primary
        model_id = body.get("model")
        if model_id:
            model = db.get_model(model_id)
            if not model:
                return {"error": "not_found", "message": f"Model {model_id} not found"}, 404
            if model.get("status") not in ("ACTIVE", "AVAILABLE"):
                return {"error": "model_not_available", "message": f"Model {model_id} is not available (status: {model.get('status')})"}, 400
        else:
            primary = get_current_primary()
            if not primary:
                return {"error": "no_active_model", "message": "No active free coding model"}, 503
            model_id = primary["model_id"]

        # Check circuit breaker
        is_open, remaining = db.circuit_breaker_check(model_id)
        if is_open:
            # Try to get next available model
            available = get_available_models()
            if not available:
                return {"error": "circuit_breaker_open", "model_id": model_id, "cooldown_remaining_ms": remaining}, 503
            model_id = available[0]["model_id"]

        # Run inference
        try:
            from .validator import run_inference as do_inference
            import time
            start = time.time()
            success, content, latency_ms = do_inference(model_id, prompt, timeout=120)
            elapsed_ms = (time.time() - start) * 1000

            if success:
                db.record_request(model_id, True, latency_ms)
                return {
                    "success": True,
                    "model_id": model_id,
                    "content": content,
                    "latency_ms": round(latency_ms, 1),
                    "elapsed_ms": round(elapsed_ms, 1),
                }, 200
            else:
                db.record_request(model_id, False, latency_ms, error=content)
                # Record circuit breaker failure
                should_open, failures = db.circuit_breaker_record_failure(model_id)
                return {
                    "success": False,
                    "model_id": model_id,
                    "error": content,
                    "latency_ms": round(latency_ms, 1),
                    "circuit_open": should_open,
                }, 502
        except Exception as e:
            logger.error(f"Inference error for {model_id}: {e}")
            db.record_request(model_id, False, 0, error=str(e))
            return {"error": "inference_failed", "message": str(e)}, 500

    def get_events(self, limit: int = 100) -> tuple[dict, int]:
        """Get recent events."""
        events = db.get_events(limit=limit)
        return {"events": events, "count": len(events)}, 200

    def get_logs(self, lines: int = 100) -> tuple[dict, int]:
        """Get recent logs."""
        try:
            if LOG_PATH.exists():
                log_content = LOG_PATH.read_text()
                log_lines = log_content.strip().split("\n")[-lines:]
                return {"logs": log_lines, "count": len(log_lines)}, 200
            else:
                return {"logs": [], "count": 0, "message": "No logs yet"}, 200
        except Exception as e:
            return {"error": str(e)}, 500

    def get_history(self) -> tuple[dict, int]:
        """Get failover/promotion history."""
        # Get all events of relevant types
        events = db.get_events(limit=500)

        relevant_types = ("FAILOVER", "FAILOVER_ACTIVE", "PROMOTION", "AVAILABLE", "ACTIVE", "DEGRADED", "FAILING")
        history = [e for e in events if e.get("event_type") in relevant_types]

        return {"history": history, "count": len(history)}, 200

    def test_system(self) -> tuple[dict, int]:
        """Test the notification system."""
        telegram_ok, telegram_msg = test_telegram_connection()
        omniroute_ok = test_omniroute_endpoint()

        # Try a quick inference test
        from .validator import run_inference
        try:
            success, _, _ = run_inference("openai/gpt-4o-mini", "Say 'test' in one word.", timeout=10)
            inference_ok = success
        except Exception:
            inference_ok = False

        return {
            "telegram": {"ok": telegram_ok, "message": telegram_msg},
            "omniroute": {"ok": omniroute_ok},
            "inference": {"ok": inference_ok}
        }, 200


class APIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Free Model API."""

    api = FreeModelAPI()

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        url = urlparse(self.path)
        path = url.path
        query = parse_qs(url.query)

        # Telegram command support
        if query.get("cmd") or query.get("command"):
            cmd = (query.get("cmd") or query.get("command"))[0]
            result, status = self.handle_telegram_command(cmd, query)
        else:
            result, status = self.api.handle_request("GET", path)

        self.send_json_response(result, status)

    def do_POST(self):
        from urllib.parse import urlparse
        import json as json_lib

        url = urlparse(self.path)
        path = url.path

        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        body = None
        if content_length > 0:
            try:
                body = json_lib.loads(self.rfile.read(content_length))
            except Exception:
                pass

        result, status = self.api.handle_request("POST", path, body)
        self.send_json_response(result, status)

    def handle_telegram_command(self, cmd: str, query: dict) -> tuple[dict, int]:
        """Handle Telegram-style commands."""
        cmd = cmd.lower().strip()

        if cmd in ("status", ""):
            return self.api.status()
        elif cmd == "models":
            return self.api.list_models()
        elif cmd == "active":
            primary = get_current_primary()
            return {"primary": primary}, 200
        elif cmd == "pool":
            return self.api.get_pool()
        elif cmd == "test":
            return self.api.test_system()
        elif cmd == "discover":
            return self.api.trigger_discover()
        elif cmd.startswith("benchmark "):
            model_id = cmd[10:].strip()
            return self.api.run_benchmark(model_id)
        elif cmd.startswith("promote "):
            model_id = cmd[8:].strip()
            return self.api.promote_model(model_id)
        elif cmd.startswith("disable "):
            model_id = cmd[8:].strip()
            return self.api.disable_model(model_id)
        elif cmd == "failover":
            return self.api.manual_failover()
        elif cmd == "logs":
            lines = int(query.get("lines", ["100"])[0])
            return self.api.get_logs(lines)
        elif cmd == "history":
            return self.api.get_history()
        else:
            return {
                "error": "unknown_command",
                "message": f"Unknown command: {cmd}",
                "available": ["status", "models", "active", "pool", "test", "discover", "benchmark <model>", "promote <model>", "disable <model>", "failover", "logs", "history"]
            }, 400

    def send_json_response(self, data: dict, status: int):
        """Send JSON response."""
        import json as json_lib
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json_lib.dumps(data).encode())


def run_api_server(port: int = None):
    """Run the API server."""
    port = port or FREE_CODING_PORT

    server = HTTPServer(("0.0.0.0", port), APIHandler)
    logger.info(f"Free Model Manager API listening on port {port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("API server shutting down")
        server.shutdown()


if __name__ == "__main__":
    run_api_server()
