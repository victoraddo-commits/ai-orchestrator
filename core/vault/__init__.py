"""KAI Unified Vault 2.0 package — secret broker + capability model.

See docs/directives/2026-09-13-kai-unified-vault-2-directive.md.
"""
from core.vault.broker import Capability, Denied, SecretBroker

__all__ = ["Capability", "Denied", "SecretBroker"]
