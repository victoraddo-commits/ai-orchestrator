"""Trusted-peer gate for the auth-proxy identity headers (``X-Kai-User``).

The Command Center browser never holds the bridge token; it reaches the API
through an authenticating reverse proxy which injects a verified
``X-Kai-User`` / ``X-Kai-User-Id`` pair. Those headers are only meaningful when
they were injected by that proxy, so this module accepts them **only** when the
direct TCP peer is in a trusted allowlist.

Allowlist defaults to loopback plus the peers already permitted to reach the
API on ``:8000``; override with ``KAI_TRUSTED_PROXIES`` (comma-separated
IPs/CIDRs). Client-supplied ``X-Forwarded-For`` / ``X-Real-IP`` are honoured
**only** from a trusted peer -- an untrusted source can never use them to
launder itself into the allowlist.

The trust decision is made on the *direct peer*, not the forwarded client:
the proxy is what authenticates and injects the identity, so the browser's own
address is irrelevant (and usually not in the allowlist).
"""
from __future__ import annotations

import ipaddress
import logging
import os

logger = logging.getLogger("security.trusted_proxy")

# Peers allowed to reach the API on :8000 today (loopback + the known proxies).
DEFAULT_TRUSTED_PROXIES = ",".join((
    "127.0.0.1/8",
    "::1/128",
    "192.168.99.11",
    "192.168.1.105",
    "192.168.1.114",
    "192.168.1.112",
    "192.168.1.110",
))


def trusted_networks() -> list:
    """Parsed allowlist (never raises; invalid entries are logged + skipped)."""
    raw = os.environ.get("KAI_TRUSTED_PROXIES") or DEFAULT_TRUSTED_PROXIES
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("ignoring invalid KAI_TRUSTED_PROXIES entry %r", item)
    return networks


def direct_peer(request) -> str:
    """The TCP peer address, with the test client mapped to loopback."""
    peer = ""
    try:
        peer = (request.client.host or "") if request.client else ""
    except Exception:  # noqa: BLE001 - never let an odd request object raise
        peer = ""
    if peer in ("", "testclient", "localhost"):
        return "127.0.0.1"
    return peer


def is_trusted_peer(request) -> bool:
    """True when the direct connection comes from an allowlisted peer."""
    peer = direct_peer(request)
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(ip in network for network in trusted_networks())


def client_ip(request) -> str:
    """Effective client IP.

    ``X-Forwarded-For`` / ``X-Real-IP`` are consulted only when the direct peer
    is trusted; from an untrusted source they are ignored and the peer address
    is returned, so a spoofed header cannot grant trust.
    """
    peer = direct_peer(request)
    if is_trusted_peer(request):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    return peer


def proxy_identity(request, x_kai_user: str | None,
                   x_kai_user_id: str | None) -> str | None:
    """Return ``auth-proxy:<id>`` for a trusted peer, else ``None``.

    Both headers are required (a stray one is never treated as identity).
    """
    if not (x_kai_user and x_kai_user_id):
        return None
    if not is_trusted_peer(request):
        logger.warning(
            "rejected X-Kai-User identity from untrusted peer %s",
            direct_peer(request))
        return None
    return f"auth-proxy:{x_kai_user_id}"
