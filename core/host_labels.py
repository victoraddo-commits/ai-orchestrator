"""Host-qualified labels for CTs and VMs across the Kai estate.

VMIDs are only unique *per Proxmox host*, so a bare ``CT102`` is ambiguous —
Proxmox A, B and C each run a container 102. Every guest is therefore labelled
``<KIND><vmid>-<SITE>``:

    CT102-PA   container 102 on Proxmox A
    CT102-PB   container 102 on Proxmox B
    VM112-PB   VM 112 on Proxmox B

``SITE`` is one of ``PA``/``PB``/``PC`` (Proxmox A/B/C), or ``PX`` when the host
cannot be identified. The mapping is intentionally token-based (hostname, IP,
or the ``pve-a``/``pve-b``/``pve-c`` aliases) because the estate exposes a guest
under different names depending on which collector produced it.
"""
from __future__ import annotations

import ipaddress
import re

SITE_UNKNOWN = "PX"

# Canonical display names for each site.
SITE_HOSTS = {"PA": "Proxmox A", "PB": "Proxmox B", "PC": "Proxmox C"}

# Ordered token rules: (site, tokens). A token matches when it appears as a
# whole label segment (``pve-b``, ``proxmox-b``, ``192.168.1.110``,
# ``100.122.38.118`` …). Longest/most specific tokens are listed first.
_SITE_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("PB", ("pve-b", "proxmox-b", "pve-b.", "pveb",
            "192.168.1.110", "100.122.38.118")),
    ("PA", ("pve-a", "proxmox-a", "pve-a.", "pvea",
            "192.168.99.2", "100.83.4.27")),
    ("PC", ("pve-c", "proxmox-c", "pve-c.", "pvec",
            "100.116.165.100",
            "192.168.66.2", "192.168.66.3")),
)

# Bare ``pve`` is genuinely ambiguous (the node name is reused); the estate's
# canonical mapping is Proxmox A for the bare alias in the world model, but we
# only fall back to it after the explicit tokens above.
_AMBIGUOUS = {"pve", "proxmox"}

_GUEST_KINDS = {"LXC": "CT", "CONTAINER": "CT", "CT": "CT", "VM": "VM"}


def _norm(value: str | None) -> str:
    return str(value or "").strip().lower()


def host_site(*candidates: str | None) -> str:
    """Return the site suffix (``PA``/``PB``/``PC``/``PX``) for any host hints.

    Pass as many hints as are available (name, IP, id); the first one that
    resolves wins, so callers can hand over ``("pve-b", host_ip)``.
    """
    for cand in candidates:
        low = _norm(cand)
        if not low:
            continue
        # Normalise an IP:port or bare hostname into matchable tokens.
        token = low.split("/")[0].strip()
        try:
            ip = ipaddress.ip_address(token)
        except ValueError:
            ip = None
        for site, tokens in _SITE_TOKENS:
            for t in tokens:
                try:
                    tip = ipaddress.ip_address(t)
                except ValueError:
                    tip = None
                if tip is not None:
                    if ip is not None and ip.version == tip.version and ip == tip:
                        return site
                elif t in token or token in t:
                    return site
    return SITE_UNKNOWN


def normalize_kind(kind: str | None) -> str:
    """Map an LXC/container/CT/VM hint to the canonical ``CT`` or ``VM``."""
    key = _norm(kind).upper().replace(" ", "")
    return _GUEST_KINDS.get(key, key or "GUEST")


def guest_label(kind: str | None, vmid: int | str | None,
                host: str | None = None, name: str | None = None,
                site: str | None = None) -> str:
    """Build ``<KIND><vmid>-<SITE>`` (e.g. ``CT102-PB``).

    ``site`` may be supplied directly (already-resolved); otherwise it is
    derived from ``host``/``name``. If the vmid already ends with a site
    suffix the label is returned unchanged, so the helper is idempotent.
    """
    raw = str(vmid if vmid is not None else "").strip()
    if raw and re.search(r"-(PA|PB|PC|PX)$", raw):
        return raw
    k = normalize_kind(kind)
    resolved = site or host_site(host, name)
    return f"{k}{raw}-{resolved}"


def site_label(site: str | None) -> str:
    """Human label for a site suffix (``PB`` -> ``Proxmox B``)."""
    key = (site or SITE_UNKNOWN).upper()
    return SITE_HOSTS.get(key, f"Proxmox {key}")


def label_host(host: dict) -> dict:
    """Return a copy of an infra-usage ``host`` dict with ``site`` added."""
    if not isinstance(host, dict):
        return host
    site = host.get("site") or host_site(host.get("name"), host.get("host"))
    out = dict(host)
    out["site"] = site
    out["site_label"] = site_label(site)
    return out


def label_guest(guest: dict) -> dict:
    """Return a copy of a guest dict with ``site``/``label`` added.

    Idempotent: an existing ``label``/``site`` is preserved.
    """
    if not isinstance(guest, dict):
        return guest
    site = guest.get("site") or host_site(guest.get("host"), guest.get("host_name"),
                                          guest.get("name"))
    out = dict(guest)
    out["site"] = site
    out["label"] = guest.get("label") or guest_label(
        guest.get("kind"), guest.get("vmid"), guest.get("host"),
        guest.get("name"), site=site)
    return out
