"""Tool-bus wrappers for the SMS worker (additive, idempotent).

Registers ``kai.sms.*`` tools so the Brain / Mission Engine can discover and run
SMS capabilities through the policy-gated tool bus. The OTP **hand-off is not
exposed here** — codes are consumed only by the in-process onboarding flow, never
returned across the tool bus (where they could be logged).
"""

from __future__ import annotations


def register_sms_tools() -> list[str]:
    from core.kai_tools.registry import CONTROLLED, SAFE, ToolSpec, REGISTRY

    from core.sms import health, ingest_raw, list_lines
    from core.sms.adapter import RawSms

    specs = [
        ToolSpec(
            id="kai.sms.list_lines", name="List SMS lines",
            description="List configured SMS lines (vault references only).",
            risk=SAFE, inputs={}, tags=["sms", "read"], audit=True),
        ToolSpec(
            id="kai.sms.health", name="SMS worker health",
            description="Return SMS worker health/status counters.",
            risk=SAFE, inputs={}, tags=["sms", "read"], audit=True),
        ToolSpec(
            id="kai.sms.ingest", name="Ingest an SMS",
            description="Normalize, classify and persist one inbound SMS (OTP redacted).",
            risk=CONTROLLED, inputs={"from": "str", "to": "str", "body": "str"},
            tags=["sms", "write"], permissions={"filesystem": ["memory"]}, audit=True),
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
        from core.sms import health as _health, ingest_raw, list_lines
        from core.sms.adapter import RawSms

        if tool_id == "kai.sms.list_lines":
            return [l.model_dump() for l in list_lines()]
        if tool_id == "kai.sms.health":
            return _health()
        if tool_id == "kai.sms.ingest":
            raw = RawSms(from_number=args["from"], to_number=args.get("to"),
                         body=args["body"], timestamp=args.get("timestamp"))
            return ingest_raw(raw).model_dump()
        raise KeyError(tool_id)

    return _run


__all__ = ["register_sms_tools"]
