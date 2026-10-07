"""Kai Vault machine-plane bridge for the orchestrator credential system.

Same contract as kai-betting's vault_client (deployed 2026-08-22): bearer
token from VAULT_BEARER_TOKEN env or VAULT_TOKEN_FILE, POST /api/v1/machine/
secret with operation=reveal, values only ever returned — never logged.
Add-only: a None return means "no vault value", and every caller falls back
to the local encrypted store. The vault being down must never block work.

Secret path convention: ai-orchestrator/providers/<provider-slug>.
"""
from __future__ import annotations

import logging
import os
import ssl
from typing import Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

# Direct to kai-vault on Proxmox B CT107, reachable over TLS at
# https://192.168.1.107:8443. The previous default (192.168.1.117:8120)
# pointed at a host that does not exist, which spammed
# "kai-vault unreachable" every scheduler cycle.
DEFAULT_VAULT_URL = "https://192.168.1.107:8443"

# Internal CA for the vault's self-signed machine-plane certificate. The
# ai-orchestrator-api unit already drops this in via VAULT_CA_BUNDLE.
DEFAULT_VAULT_CA_BUNDLE = "/etc/kai/tls/vault-mp.crt"

# Hosts allowed to use the explicit "insecure internal" TLS fallback when no
# CA bundle is configured. Deliberately scoped to known internal hosts only —
# this is never a global verification disable.
_INSECURE_INTERNAL_HOSTS = frozenset({"192.168.1.107"})

VAULT_TOKEN_FILE = os.environ.get(
    "VAULT_TOKEN_FILE", "/root/.credentials/ai-orchestrator-vault-token")
VAULT_TIMEOUT = float(os.environ.get("VAULT_TIMEOUT", "5"))


def vault_url() -> str:
    """Base URL for kai-vault: VAULT_URL env wins, else the working default."""
    return (os.environ.get("VAULT_URL") or DEFAULT_VAULT_URL).rstrip("/")


# Backwards-compatible import-time snapshot. Prefer vault_url() for new code
# so a VAULT_URL set after import is still honoured.
VAULT_URL = vault_url()


def _ca_bundle() -> Optional[str]:
    """Return the configured CA bundle path if it exists on disk, else None."""
    configured = os.environ.get("VAULT_CA_BUNDLE", DEFAULT_VAULT_CA_BUNDLE)
    if configured and os.path.exists(configured):
        return configured
    return None


def _verify_for(url: str):
    """TLS verification setting for *url*.

    Prefer the internal CA bundle. When it is missing, fall back to an
    explicit insecure mode scoped to the known internal vault host only;
    any other host keeps full certificate verification.
    """
    ca = _ca_bundle()
    if ca:
        return ca
    host = urlsplit(url).hostname or ""
    if host in _INSECURE_INTERNAL_HOSTS:
        logger.warning(
            "kai-vault: CA bundle unavailable; using insecure TLS for known "
            "internal host %s", host)
        return False
    return True


def ssl_context_for(url: str) -> Optional[ssl.SSLContext]:
    """Return an SSLContext matching _verify_for for urllib-based callers.

    None for plain http so callers can pass ``context=None`` unchanged.
    """
    if urlsplit(url).scheme != "https":
        return None
    verify = _verify_for(url)
    if verify is False:
        return ssl._create_unverified_context()
    if isinstance(verify, str):
        return ssl.create_default_context(cafile=verify)
    return ssl.create_default_context()


def _slug(provider: str) -> str:
    return provider.strip().lower().replace("-", "_").replace(" ", "_").replace(".", "_")


def secret_path_for_provider(provider: str) -> str:
    return f"ai-orchestrator/providers/{_slug(provider)}"


def load_token() -> Optional[str]:
    tok = os.environ.get("VAULT_BEARER_TOKEN", "").strip()
    if tok:
        return tok
    try:
        with open(VAULT_TOKEN_FILE, "r", encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def fetch_secret(path: str, token: str) -> Optional[str]:
    """Reveal one secret. None on ANY failure. Value never logged."""
    url = f"{vault_url()}/api/v1/machine/secret"
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"path": path, "operation": "reveal",
                  "reason": "orchestrator credential resolution"},
            timeout=VAULT_TIMEOUT,
            verify=_verify_for(url),
        )
        if response.status_code == 200:
            return response.json().get("value")
        logger.warning("kai-vault: %s -> HTTP %d (fallback to local vault)",
                       path, response.status_code)
    except requests.RequestException as error:
        logger.warning("kai-vault unreachable (%s) — fallback to local vault",
                       type(error).__name__)
    return None


def fetch_for_provider(provider: str) -> Optional[str]:
    token = load_token()
    if not token:
        return None
    return fetch_secret(secret_path_for_provider(provider), token)


def delete_for_provider(provider: str, token: Optional[str] = None) -> bool:
    """Best-effort delete of a provider secret from the kai-vault machine plane.

    Mirrors :func:`fetch_for_provider` — same path convention and bearer
    token — but sends ``operation="delete"``. Values are never logged.

    The machine plane as deployed (CT107) implements only reveal/set for a
    static service subject: it ignores unknown operations and returns the
    value for any existing path with HTTP 200. HTTP 200 is therefore NOT
    proof of deletion; this returns True only when the response body
    explicitly confirms it (``{"deleted": true}``). A False return means the
    kai-vault copy must be considered intact (token cannot delete, path
    absent, or vault unreachable).
    """
    token = token or load_token()
    if not token:
        return False

    path = secret_path_for_provider(provider)
    url = f"{vault_url()}/api/v1/machine/secret"
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"path": path, "operation": "delete",
                  "reason": "orchestrator credential deletion"},
            timeout=VAULT_TIMEOUT,
            verify=_verify_for(url),
        )
    except requests.RequestException as error:
        logger.warning("kai-vault delete unreachable (%s) — copy not removed",
                       type(error).__name__)
        return False

    if response.status_code == 200:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and body.get("deleted") is True:
            return True
        logger.warning(
            "kai-vault delete NOT confirmed for %s (machine plane does not "
            "implement deletion) — copy not removed", path)
        return False

    logger.warning("kai-vault delete: %s -> HTTP %d (copy not removed)",
                   path, response.status_code)
    return False
