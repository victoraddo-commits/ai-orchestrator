"""AI-4: Serverless Endpoints — callable HTTP wrappers for text providers.

Deployable as standalone serverless functions (Vercel, Netlify, AWS Lambda).
Each endpoint wraps the existing ai_router.delegate() with minimal cold-start overhead.

Architecture:
    HTTP Request → handler(request) → ai_router.delegate() → provider → OpenAI-compatible response
"""

from .handler import handle_completion, handle_health

# vercel_handler is a deployable serverless function module (not an importable
# object) — it provides a BaseHTTPRequestHandler subclass for Vercel's runtime.
# Deploy as /api/ai_completion.py or any /api/*.py Vercel Function path.

__all__ = ["handle_completion", "handle_health"]
