"""core.sms worker service — pull loop + HTTP webhook host.

Two ways SMS reaches the worker:

* **push** (production): the webhook server (:mod:`core.sms.server`) receives a
  POST from an external gateway or the Android SMS-forwarder app. Run it with
  ``kai-sms-worker.service``.
* **pull** (tests/fixtures): a :class:`~core.sms.adapter.FixtureAdapter` reads a
  directory of JSON messages; :func:`run_once` drains it and records status to
  ``memory/sms_watch_status.json``.

No secret is logged; the webhook token is resolved from the vault into memory.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from core.sms import manager
from core.sms.adapter import FixtureAdapter, GatewayAdapter
from core.sms.schema import now_iso

logger = logging.getLogger("kai.sms")

DEFAULT_HOST = os.environ.get("KAI_SMS_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.environ.get("KAI_SMS_PORT", "8770"))


class _EmptyAdapter(GatewayAdapter):
    def receive(self, limit: int = 50):
        return []

    def ingest_webhook(self, payload: dict, *, token=None):  # pragma: no cover
        from core.sms.adapter import parse_payload

        return parse_payload(payload)


def build_fixture_adapter(directory: Optional[str] = None) -> Optional[FixtureAdapter]:
    directory = directory or os.environ.get("KAI_SMS_FIXTURE_DIR")
    return FixtureAdapter(directory) if directory else None


def run_once(adapter: Optional[GatewayAdapter] = None, *,
             fixture_dir: Optional[str] = None) -> dict:
    adapter = adapter or build_fixture_adapter(fixture_dir) or _EmptyAdapter()
    summary = manager.poll_once(adapter)
    _write_status(summary)
    return summary


def _write_status(summary: dict) -> None:
    payload = {"updated_at": now_iso(), "last_run": summary, "health": manager.health()}
    from core.memory import save

    save("sms_watch_status.json", payload, directory=store_memory_dir())


def store_memory_dir():
    from core.sms import store

    return store.memory_dir()


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:  # pragma: no cover
    import uvicorn

    from core.sms.server import create_app

    # Phase 2c: money SMS bridge subscriber — the SMS events originate in THIS
    # process's event bus, so the subscriber must attach here too. Idempotent
    # + fail-safe: a bridge problem must never stop the webhook server.
    try:
        from core.money_sms import bridge as _money_sms_bridge
        _money_sms_bridge.register_subscriber()
        logger.info("money sms bridge subscriber registered (sms worker process)")
    except Exception as _mms_exc:
        logger.warning("money sms bridge registration failed: %s",
                       type(_mms_exc).__name__)

    token = None
    from core.sms import vault

    token = vault.load_webhook_token()
    if not token:
        logger.warning("sms worker: no webhook token configured (vault secrets/sms/webhook_token "
                       "or KAI_SMS_WEBHOOK_TOKEN); deliveries will be rejected")
    uvicorn.run(create_app(token=token), host=host, port=port, log_level="info")


__all__ = ["run_once", "serve", "build_fixture_adapter", "DEFAULT_HOST", "DEFAULT_PORT"]
