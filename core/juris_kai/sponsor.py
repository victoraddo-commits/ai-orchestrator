"""Contextual, feature-flagged sponsor slot for the free tier (Phase 7 T6).

A single sponsor line can be shown **alongside** a free-tier answer as a
separate trailing block. It is deliberately constrained:

  * **Off by default** — nothing renders until an operator enables it.
  * **Free tier only** — never on any paid tier.
  * **Never inside an answer** — callers append it to the delivered text *after*
    the answer is recorded, so it can never leak into the stored/learning-loop
    answer.
  * **Non-behavioural** — no tracking, no per-user targeting; the first active
    sponsor is chosen deterministically.
  * **Configurable** — a small JSON config (``memory/juris_sponsors.json``) or
    the ``JURIS_SPONSOR_ENABLED`` / ``JURIS_SPONSORS`` environment variables.

Security: NO imports of ``core.build_manager``, ``core.approval`` or
``core.deployment_manager``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from core.juris_kai import entitlements as _entitlements

logger = logging.getLogger("juris_kai.sponsor")

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = str(_REPO_ROOT / "memory" / "juris_sponsors.json")
SPONSORS_VERSION = 1

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": SPONSORS_VERSION,
    "enabled": False,
    "sponsors": [],
    "updated_at": None,
    "updated_by": None,
}

_TRUTHY = {"1", "true", "yes", "on"}


def config_path() -> str:
    return os.environ.get("JURIS_SPONSORS_PATH", "") or DEFAULT_PATH


def _clean_sponsor(raw: Any) -> Dict[str, str]:
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split("|")]
        raw = {"name": parts[0] if parts else "",
               "tagline": parts[1] if len(parts) > 1 else "",
               "url": parts[2] if len(parts) > 2 else ""}
    if not isinstance(raw, dict):
        return {}
    name = str(raw.get("name") or "").strip()
    if not name:
        return {}
    return {"name": name,
            "tagline": str(raw.get("tagline") or "").strip(),
            "url": str(raw.get("url") or "").strip()}


def _clean_config(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(DEFAULT_CONFIG)
    sponsors = [_clean_sponsor(s) for s in (raw.get("sponsors") or [])]
    return {
        "version": SPONSORS_VERSION,
        "enabled": bool(raw.get("enabled", False)),
        "sponsors": [s for s in sponsors if s],
        "updated_at": raw.get("updated_at"),
        "updated_by": raw.get("updated_by"),
    }


def load_config() -> Dict[str, Any]:
    """Load the sponsor config, falling back to defaults on any error."""
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except (OSError, ValueError):
        logger.warning("juris sponsor: unreadable config; using defaults")
        return dict(DEFAULT_CONFIG)
    return _clean_config(raw)


def save_config(config: Dict[str, Any], updated_by: str = "") -> Dict[str, Any]:
    """Validate + persist the sponsor config atomically."""
    doc = _clean_config(config or {})
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    doc["updated_by"] = updated_by or None
    path = config_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return doc


def _env_sponsors() -> List[Dict[str, str]]:
    raw = os.environ.get("JURIS_SPONSORS", "").strip()
    if not raw:
        return []
    parsed: Any = raw
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = [chunk for chunk in raw.split(";") if chunk.strip()]
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    return [s for s in (_clean_sponsor(item) for item in parsed) if s]


def active_sponsors() -> List[Dict[str, str]]:
    """The configured sponsors (env overrides the file)."""
    env = _env_sponsors()
    if env:
        return env
    return list(load_config().get("sponsors") or [])


def enabled() -> bool:
    """Whether the sponsor slot is on (env overrides the file)."""
    env = os.environ.get("JURIS_SPONSOR_ENABLED")
    if env is not None:
        return env.strip().lower() in _TRUTHY
    return bool(load_config().get("enabled"))


def is_free_tier(tier_key: str) -> bool:
    return _entitlements.is_free_tier(tier_key)


def _format(sponsor: Dict[str, str]) -> str:
    name = sponsor.get("name", "")
    tagline = sponsor.get("tagline", "")
    url = sponsor.get("url", "")
    line = f"Sponsored: {name}"
    if tagline:
        line += f" — {tagline}"
    if url:
        line += f" ({url})"
    return line


def footer_for(tier_key: str) -> str:
    """The sponsor footer for a plan, or "" when it must not render.

    Returns "" unless the slot is enabled, the plan is a free tier, and at
    least one sponsor is configured. Selection is deterministic (first active
    sponsor) — no per-user behaviour.
    """
    if not enabled():
        return ""
    if not is_free_tier(tier_key):
        return ""
    sponsors = active_sponsors()
    if not sponsors:
        return ""
    return _format(sponsors[0])


def attach(text: str, tier_key: str) -> str:
    """Append the sponsor footer to delivered text, as a separate block.

    When the slot is disabled or the tier is paid, ``text`` is returned
    unchanged. The footer is always a distinct trailing block (``\\n\\n``
    separated), never woven into the answer body.
    """
    footer = footer_for(tier_key)
    if not footer:
        return text
    return f"{text}\n\n{footer}"
