"""Phase 18A-b: Security Headers Middleware.

Starlette/FastAPI middleware that injects security headers into every response:
- Content-Security-Policy (CSP)
- Strict-Transport-Security (HSTS)
- X-Content-Type-Options
- X-Frame-Options
- X-XSS-Protection
- Referrer-Policy
- Permissions-Policy

Headers are configurable via environment variables for deployment flexibility.
"""

import os
from typing import Callable, Awaitable
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Default security headers
# ---------------------------------------------------------------------------

SECURITY_HEADERS = {
    # Content-Security-Policy: restrict resource loading to same origin by default.
    # Dashboard needs 'unsafe-inline' for its inline styles/scripts.
    "Content-Security-Policy": os.environ.get(
        "SECURITY_CSP",
        (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        ),
    ),

    # HSTS: enforce HTTPS for 1 year, include subdomains
    "Strict-Transport-Security": os.environ.get(
        "SECURITY_HSTS",
        "max-age=31536000; includeSubDomains",
    ),

    # Prevent MIME type sniffing
    "X-Content-Type-Options": "nosniff",

    # Prevent clickjacking
    "X-Frame-Options": "DENY",

    # XSS protection (legacy, CSP handles this, but belt-and-suspenders)
    "X-XSS-Protection": "0",  # Disable legacy filter in favor of CSP

    # Referrer policy: send origin only for same-origin
    "Referrer-Policy": "strict-origin-when-cross-origin",

    # Permissions-Policy: restrict browser features
    "Permissions-Policy": os.environ.get(
        "SECURITY_PERMISSIONS_POLICY",
        "camera=(), microphone=(), geolocation=(), payment=()",
    ),

    # Cache control for API endpoints (don't cache API responses)
    "Cache-Control": "no-store, max-age=0",

    # Server header removal
    "Server": "",  # Remove server identity
}

# Headers to explicitly remove from responses
HEADERS_TO_REMOVE = {
    "server",
    "x-powered-by",
    "x-aspnet-version",
    "x-aspnetmvc-version",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security headers into every response.

    Usage in FastAPI:
        app.add_middleware(SecurityHeadersMiddleware)

    Headers are added after the request is processed so they don't
    interfere with the application logic.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        # Remove dangerous headers
        for header in HEADERS_TO_REMOVE:
            if header in response.headers:
                del response.headers[header]

        # Add security headers
        for name, value in SECURITY_HEADERS.items():
            if value:  # Skip empty values like Server=""
                response.headers[name] = value

        return response


def get_security_headers() -> dict:
    """Return the current security header configuration (for tests/dashboard)."""
    return dict(SECURITY_HEADERS)
