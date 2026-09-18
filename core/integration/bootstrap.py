"""§37 MODULE INTEGRATION — startup exposure of module capabilities."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def start() -> dict:
    """Register every KAI module's capability with the unified workforce.

    Idempotent; safe to call from the API lifespan and from a scheduler cycle.
    """
    from core.integration.module_bridge import get_bridge
    return get_bridge().ensure_exposed()
