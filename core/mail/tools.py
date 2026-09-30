"""Tool-bus wrappers for the mail worker (additive, idempotent).

Registers ``kai.mail.*`` tools so the Brain / Mission Engine can discover and
run email capabilities through the policy-gated tool bus. Nothing here
reimplements infrastructure — each tool delegates to :mod:`core.mail`.
"""

from __future__ import annotations

from typing import Optional


def register_mail_tools() -> list[str]:
    from core.kai_tools.registry import CONTROLLED, SAFE, ToolSpec, REGISTRY

    from core.mail import health, ingest_raw, list_accounts, poll_once

    specs = [
        ToolSpec(
            id="kai.mail.list_accounts", name="List mail accounts",
            description="List configured mailbox accounts (vault references only).",
            risk=SAFE, inputs={}, tags=["mail", "read"], audit=True),
        ToolSpec(
            id="kai.mail.health", name="Mail worker health",
            description="Return mail worker health/status counters.",
            risk=SAFE, inputs={}, tags=["mail", "read"], audit=True),
        ToolSpec(
            id="kai.mail.ingest", name="Ingest a raw email",
            description="Parse, validate, classify and persist one raw RFC822 message.",
            risk=CONTROLLED, inputs={"raw": "str"}, tags=["mail", "write"],
            permissions={"filesystem": ["memory"]}, audit=True),
        ToolSpec(
            id="kai.mail.verify_flow", name="Run email verification flow",
            description="Drive verification automation for an ingested verification email.",
            risk=CONTROLLED, inputs={"email_id": "str"},
            tags=["mail", "verification", "browser"],
            permissions={"network": ["provider"]}, audit=True),
    ]

    registered: list[str] = []
    for spec in specs:
        if REGISTRY.get(spec.id) is not None:
            continue
        REGISTRY.register(spec, _handler(spec.id))
        registered.append(spec.id)
    return registered


def _handler(tool_id: str):
    def _run(args: dict):
        from core.mail import health as _health, ingest_raw as _ingest, verify_email_flow
        from core.mail.manager import get_email

        if tool_id == "kai.mail.list_accounts":
            from core.mail import list_accounts

            return [a.model_dump() for a in list_accounts()]
        if tool_id == "kai.mail.health":
            return _health()
        if tool_id == "kai.mail.ingest":
            return _ingest(args["raw"]).model_dump()
        if tool_id == "kai.mail.verify_flow":
            email = get_email(args["email_id"])
            if email is None:
                return {"handled": False, "reason": "email_not_found"}
            return verify_email_flow(email)
        raise KeyError(tool_id)

    return _run


__all__ = ["register_mail_tools"]
