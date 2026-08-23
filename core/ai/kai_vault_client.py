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
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Default routes through the persistent SSH tunnel (kai-vault-tunnel.service,
# 127.0.0.1:18120 -> CT107 192.168.1.117:8120): CT107's port has no DNAT rule
# on network-core-b, so 192.168.1.117 is not directly reachable from this LXC.
# If a DNAT rule for 8120 is ever added to CT102 (10.250.0.2), switch to it.
VAULT_URL = os.environ.get("VAULT_URL", "http://127.0.0.1:18120")
VAULT_TOKEN_FILE = os.environ.get(
    "VAULT_TOKEN_FILE", "/root/.credentials/ai-orchestrator-vault-token")
VAULT_TIMEOUT = float(os.environ.get("VAULT_TIMEOUT", "5"))


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
    try:
        response = requests.post(
            f"{VAULT_URL}/api/v1/machine/secret",
            headers={"Authorization": f"Bearer {token}"},
            json={"path": path, "operation": "reveal",
                  "reason": "orchestrator credential resolution"},
            timeout=VAULT_TIMEOUT,
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
