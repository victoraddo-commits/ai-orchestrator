"""core.mail worker service — the ``mail_watch`` poll loop.

Runs on CT111 as ``kai-mail-worker.service``. Each cycle it polls the configured
mailbox (Proton Bridge localhost IMAP once Bridge is live; a stub otherwise),
ingests mail, and drives verification automation. Status is written to
``memory/mail_watch_status.json`` for the Command Center / health checks.

Secrets are resolved from the vault into memory only and never logged.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from core.mail import manager, store
from core.mail.schema import now_iso
from core.mail.transports import StubTransport

logger = logging.getLogger("kai.mail")

DEFAULT_INTERVAL = float(os.environ.get("KAI_MAIL_POLL_INTERVAL", "60"))


def build_inbound_transport(account):
    """Construct the IMAP transport for *account*, resolving creds from the vault."""
    from core.mail.transports import IMAPTransport
    from core.mail.vault import load_transport_credentials

    creds = load_transport_credentials(account.vault_reference) if account.vault_reference else None
    if creds is None:
        return None
    return IMAPTransport(
        account.imap_host, account.imap_port, ssl=account.imap_ssl,
        user=creds.user or account.address, password=creds.password,
        folder=account.folder,
    )


def _default_account():
    accounts = [a for a in manager.list_accounts() if a.enabled]
    return accounts[0] if accounts else None


def run_once(account_id: Optional[str] = None, *, transport=None,
             browser_client=None) -> dict:
    account = manager.get_account(account_id) if account_id else _default_account()
    if transport is None and account is not None:
        transport = build_inbound_transport(account)
    if transport is None:
        transport = StubTransport([])  # idle safely until Bridge is configured

    summary = manager.poll_once(
        transport, browser_client=browser_client,
        account_id=account.account_id if account else None,
        official_domains=account.official_domains if account else None,
    )
    _write_status(summary)
    return summary


def _write_status(summary: dict) -> None:
    payload = {"updated_at": now_iso(), "last_run": summary, "health": manager.health()}
    from core.memory import save

    save("mail_watch_status.json", payload, directory=store.memory_dir())


def serve(interval: float = DEFAULT_INTERVAL, once: bool = False) -> None:
    while True:
        try:
            summary = run_once()
            logger.info("mail_watch: fetched=%s verified=%s suspicious=%s",
                        summary.get("fetched"), summary.get("verified"),
                        summary.get("suspicious"))
        except Exception as exc:  # keep the worker alive; never crash the loop
            logger.warning("mail_watch cycle failed: %s", type(exc).__name__)
        if once:
            break
        time.sleep(max(5.0, interval))


__all__ = ["build_inbound_transport", "run_once", "serve", "DEFAULT_INTERVAL"]
