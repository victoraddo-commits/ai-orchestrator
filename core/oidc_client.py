"""OIDC client for kai-vault SSO flow.

Environment variables:
    KAI_ID_VAULT_URL       - vault base URL (default: https://vault.sso.deerude.com)
    KAI_ID_CLIENT_ID       - OIDC client ID (default: ai-orchestrator)
    KAI_ID_SECRET_FILE     - path to file containing client_secret (PREFERRED)
    KAI_ID_SECRET          - fallback env var for client_secret
    KAI_ID_CALLBACK_URL    - redirect URI (default: https://kai.lan/auth/kai/callback)
    KAI_ID_ALLOW_SELF_SIGNED - if "1", disable SSL verification for self-signed vault cert
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class OIDCError(Exception):
    """Vault returned an error or is unreachable."""
    pass


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OIDCClient:
    """Manages the OIDC authorization code flow against kai-vault."""

    VAULT_URL: str = os.environ.get(
        "KAI_ID_VAULT_URL", "https://vault.sso.deerude.com"
    ).rstrip("/")
    CLIENT_ID: str = os.environ.get("KAI_ID_CLIENT_ID", "ai-orchestrator")
    CALLBACK_URL: str = os.environ.get(
        "KAI_ID_CALLBACK_URL", "https://kai.lan/auth/kai/callback"
    )
    ALLOW_SELF_SIGNED: bool = os.environ.get("KAI_ID_ALLOW_SELF_SIGNED", "") == "1"

    # Load secret from file first, fall back to env var
    _secret_file = os.environ.get("KAI_ID_SECRET_FILE", "")
    if _secret_file and os.path.isfile(_secret_file):
        with open(_secret_file) as f:
            CLIENT_SECRET = f.read().strip()
    else:
        CLIENT_SECRET = os.environ.get("KAI_ID_SECRET", "")

    # In-memory state store: state -> expires timestamp (UTC seconds)
    _state_store: dict[str, float] = {}
    STATE_TTL_SECS: int = 600  # 10 minutes

    _lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_authorization_url(self) -> tuple[str, str]:
        """Generate the vault authorization URL and a cryptographic state token.

        Returns:
            (url, state) - redirect URL for the browser and the opaque state value.
        """
        state = secrets.token_hex(32)  # 256-bit
        expires_at = time.time() + self.STATE_TTL_SECS

        with self._lock:
            self._state_store[state] = expires_at

        params = {
            "client_id": self.CLIENT_ID,
            "response_type": "code",
            "redirect_uri": self.CALLBACK_URL,
            "state": state,
            "scope": "openid profile email role",
        }
        url = f"{self.VAULT_URL}/sso/authorize?{urlencode(params)}"
        return url, state

    def validate_state(self, state: str) -> bool:
        """Check whether a state token is present and not expired.

        Removes the state after the first check (one-use, prevents replay).
        Returns True if valid and present, False otherwise.
        """
        with self._lock:
            expires_at = self._state_store.get(state)
            if expires_at is None:
                return False
            if time.time() > expires_at:
                # Expired — clean up
                self._state_store.pop(state, None)
                return False
            # Valid and present — consume it
            self._state_store.pop(state, None)
            return True

    def exchange_code(self, code: str, state: str, saved_state: str) -> dict:
        """Exchange an authorization code for user tokens.

        Args:
            code:        The authorization code from the vault redirect.
            state:       The state value received from the redirect.
            saved_state: The state value this client originally returned.

        Returns:
            {
                "user": {"id": str, "username": str, "role": str},
                "step_up_fresh": bool,
                "issued_at": int   # Unix timestamp
            }

        Raises:
            OIDCError: if state mismatch, vault unreachable, or vault returns an error.
        """
        if state != saved_state:
            raise OIDCError("state_mismatch")

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.CALLBACK_URL,
            "client_id": self.CLIENT_ID,
            "client_secret": self.CLIENT_SECRET,
        }

        try:
            resp = self._post(f"{self.VAULT_URL}/sso/token", payload)
        except OIDCError:
            raise
        except Exception as exc:
            raise OIDCError(f"vault_unreachable: {exc}") from exc

        if "error" in resp:
            msg = resp.get("error_description") or resp.get("error", "unknown")
            raise OIDCError(f"vault_error: {resp['error']} — {msg}")

        return {
            "user": {
                "id": resp.get("user_id", ""),
                "username": resp.get("username", ""),
                "role": self.map_role(resp.get("role", "")),
            },
            "step_up_fresh": resp.get("step_up_fresh", False),
            "issued_at": resp.get("issued_at", int(time.time())),
        }

    def refresh_token(self, refresh_token: str) -> dict:
        """Refresh an access token using a refresh token.

        Returns the same dict shape as :meth:`exchange_code`.
        """
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.CLIENT_ID,
            "client_secret": self.CLIENT_SECRET,
        }

        try:
            resp = self._post(f"{self.VAULT_URL}/sso/token", payload)
        except OIDCError:
            raise
        except Exception as exc:
            raise OIDCError(f"vault_unreachable: {exc}") from exc

        if "error" in resp:
            msg = resp.get("error_description") or resp.get("error", "unknown")
            raise OIDCError(f"vault_error: {resp['error']} — {msg}")

        return {
            "user": {
                "id": resp.get("user_id", ""),
                "username": resp.get("username", ""),
                "role": self.map_role(resp.get("role", "")),
            },
            "step_up_fresh": resp.get("step_up_fresh", False),
            "issued_at": resp.get("issued_at", int(time.time())),
        }

    @staticmethod
    def map_role(vault_role: str) -> str:
        """Map a vault role name to an operator-role string.

        Args:
            vault_role: Role name returned by the vault (e.g. "admin", "operator",
                        "auditor", or arbitrary/unknown).

        Returns:
            "operator" for admin/operator, "viewer" for auditor/unknown.
        """
        if vault_role in ("admin", "operator"):
            return "operator"
        return "viewer"

    def send_audit_event(
        self,
        action: str,
        actor_id: str,
        actor_type: str = "user",
        outcome: str = "success",
        detail: dict | None = None,
    ) -> None:
        """Fire-and-forget POST of an audit event to the vault.

        Silently swallows all exceptions — audit must never block authentication.
        """
        try:
            self._post(
                f"{self.VAULT_URL}/api/v1/audit/event",
                {
                    "action": action,
                    "actor_id": actor_id,
                    "actor_type": actor_type,
                    "outcome": outcome,
                    "detail": detail or {},
                },
            )
        except Exception:
            # Never let audit failures propagate
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post(self, url: str, data: dict) -> dict:
        """Send a form-urlencoded POST and return the parsed JSON response.

        Raises:
            OIDCError: on connection errors or non-2xx responses.
        """
        verify: bool | str = not self.ALLOW_SELF_SIGNED
        with httpx.Client(verify=verify) as client:
            resp = client.post(url, data=data)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                err = body.get("error", "http_error")
                desc = body.get("error_description", "")
                raise OIDCError(f"vault_error: {err} — {desc}")
            except Exception:
                resp.raise_for_status()
                raise OIDCError(f"vault_error: unexpected response {resp.status_code}")
        return resp.json()
