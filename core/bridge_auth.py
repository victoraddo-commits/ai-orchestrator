"""
Shared bridge-token authentication dependency.

Used by both core/api.py (for endpoints defined directly on the FastAPI app)
and core/klaus/api_endpoints.py (for the KLAUS router) without creating
circular imports — api.py imports from klaus/api_endpoints.py, so
klaus/api_endpoints.py CANNOT import from api.py.

This module is intentionally importable from anywhere in the tree.
"""

import hmac
import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException


API_TOKEN_PATH = Path(
    os.environ.get(
        "AI_ORCHESTRATOR_API_TOKEN_PATH",
        str(Path.home() / ".ai-orchestrator" / "api_token"),
    )
)

BRIDGE_OPERATOR = "cloudcli-plugin"

_TOKEN_CACHE: str | None = None


def _load_api_token() -> str:
    """Shared secret between core/api.py and the trusted caller (the CloudCLI
    plugin's server-side bridge, the only thing that should ever call the
    write endpoints below). Generated on first use; never derived from or
    trusted from client-supplied request data."""

    global _TOKEN_CACHE
    if _TOKEN_CACHE is not None:
        return _TOKEN_CACHE

    if not API_TOKEN_PATH.exists():
        API_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        API_TOKEN_PATH.parent.chmod(
            0o700
        )  # mkdir's mode is umask-affected; force it

        # Create with the final 0600 mode from the very first syscall -- no
        # window where the file exists with looser (e.g. default 0644)
        # permissions. O_EXCL also means this raises rather than silently
        # overwriting if another process won the race to create it first --
        # in that case just fall through and read what it wrote.
        try:
            fd = os.open(
                API_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            pass
        else:
            try:
                os.write(fd, secrets.token_urlsafe(32).encode())
            finally:
                os.close(fd)

    _TOKEN_CACHE = API_TOKEN_PATH.read_text().strip()
    return _TOKEN_CACHE


def require_bridge_token(
    authorization: str | None = Header(default=None),
) -> str:
    """Verifies the caller presented the shared secret and returns the
    identity to record as the operator -- this is the ONLY source of
    operator identity for write endpoints; it is never read from the
    request body, so a caller cannot forge who performed an action."""

    expected = f"Bearer {_load_api_token()}"
    presented = authorization or ""

    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(
            status_code=401, detail="Missing or invalid API token"
        )

    return BRIDGE_OPERATOR
