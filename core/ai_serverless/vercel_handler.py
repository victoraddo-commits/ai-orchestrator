"""AI-4: Vercel Serverless Function handler.

Entry point for Vercel Functions (Python runtime). Converts the Vercel
HTTP request/response model to the standard handler interface.

Usage:
    Deploy this module as a Vercel Function at /api/ai_completion.py
    or any /api/*.py path. Each request is stateless — the handler
    cold-starts and runs to completion within Vercel's 10s/60s timeout.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Optional

from .handler import handle_completion, handle_health


class handler(BaseHTTPRequestHandler):
    """Vercel Python runtime handler.

    Vercel expects a class named ``handler`` that extends
    BaseHTTPRequestHandler. Each HTTP request creates a new instance.
    """

    # Vercel's Python runtime has different timeout behavior on Hobby
    # (10s) vs Pro (60s) plans. We stay safe for the free tier.
    timeout = 8.0

    def do_GET(self):
        """Handle GET requests — health check only."""
        if self.path in ("/api/ai_completion", "/api/ai_completion/health"):
            self._send_json(200, handle_health())
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        """Handle POST requests — completion endpoint."""
        if self.path not in ("/api/ai_completion", "/api/ai_completion/chat"):
            self._send_json(404, {"error": "Not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "Invalid JSON body"})
            return

        prompt = payload.get("prompt") or payload.get("messages", [{}])[-1].get("content", "")
        if not prompt:
            self._send_json(400, {"error": "Missing prompt or messages"})
            return

        model = payload.get("model", "auto")
        max_tokens = payload.get("max_tokens", None)
        task_type = payload.get("task_type", None)
        temperature = payload.get("temperature", 0.7)

        result = handle_completion(
            prompt=prompt,
            model=model,
            max_tokens=max_tokens,
            task_type=task_type,
            temperature=temperature,
        )

        self._send_json(200, result)

    def _send_json(self, status_code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Suppress default stderr logging in serverless environment."""
        pass
