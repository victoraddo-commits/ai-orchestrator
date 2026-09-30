"""Tool-bus wrappers (CT111) for the browser operator.

Registers ``kai.browser.*`` tools so the Brain / Mission Engine can discover and
execute browser capabilities through the policy-gated tool bus. Nothing here
reimplements infrastructure — each tool delegates to the CT110 service via
:class:`~core.browser.client.BrowserOperatorClient`.
"""

from __future__ import annotations

from typing import Optional

from core.browser.client import BrowserOperatorClient

_SOURCE = "browser_tools"
_client: Optional[BrowserOperatorClient] = None


def default_client() -> BrowserOperatorClient:
    global _client
    if _client is None:
        _client = BrowserOperatorClient()
    return _client


def register_browser_tools(client: Optional[BrowserOperatorClient] = None) -> list[str]:
    """Register the browser tools on the KAI tool bus (idempotent)."""
    from core.kai_tools.registry import CONTROLLED, SAFE, ToolSpec, REGISTRY

    cli = client or default_client()
    specs = [
        ToolSpec(
            id="kai.browser.open_session", name="Open browser session",
            description="Open an isolated persistent browser session for a (identity, provider).",
            risk=CONTROLLED, inputs={"identity_id": "str", "provider_id": "str", "mission_id": "str"},
            tags=["browser", "onboarding"], permissions={"network": ["provider"]}, audit=True),
        ToolSpec(
            id="kai.browser.inspect", name="Inspect page",
            description="Return a sanitized, untrusted DOM/accessible snapshot + detectors.",
            risk=SAFE, inputs={"session_id": "str"}, tags=["browser", "read"], audit=True),
        ToolSpec(
            id="kai.browser.navigate", name="Navigate",
            description="Navigate a session to a URL (http/https only).",
            risk=CONTROLLED, inputs={"session_id": "str", "url": "str"},
            tags=["browser"], permissions={"network": ["provider"]}, audit=True),
        ToolSpec(
            id="kai.browser.pause_for_human", name="Pause for human takeover",
            description="Pause a mission and request operator takeover in the live browser.",
            risk=CONTROLLED, inputs={"session_id": "str", "reason": "str", "action_required": "str"},
            tags=["browser", "takeover"], audit=True),
        ToolSpec(
            id="kai.browser.resume", name="Resume after takeover",
            description="Resume a paused mission if the takeover condition is now met.",
            risk=SAFE, inputs={"session_id": "str"}, tags=["browser", "takeover"], audit=True),
        ToolSpec(
            id="kai.browser.end_session", name="End browser session",
            description="Close a browser session, persisting storage_state.",
            risk=CONTROLLED, inputs={"session_id": "str"}, tags=["browser"], audit=True),
    ]

    registered = []
    for spec in specs:
        if REGISTRY.get(spec.id) is not None:
            continue

        def _make(tool_id):
            def _fn(**kwargs):
                if tool_id == "kai.browser.open_session":
                    return cli.open_session(
                        kwargs["identity_id"], kwargs["provider_id"],
                        mission_id=kwargs.get("mission_id"))
                if tool_id == "kai.browser.inspect":
                    return cli.perform(kwargs["session_id"], "inspect")
                if tool_id == "kai.browser.navigate":
                    return cli.perform(kwargs["session_id"], "navigate", {"url": kwargs["url"]})
                if tool_id == "kai.browser.pause_for_human":
                    return cli.pause_for_human(
                        kwargs["session_id"], reason=kwargs["reason"],
                        action_required=kwargs["action_required"])
                if tool_id == "kai.browser.resume":
                    return cli.resume(kwargs["session_id"])
                if tool_id == "kai.browser.end_session":
                    return cli.end_session(kwargs["session_id"])
                raise ValueError(f"unhandled browser tool {tool_id}")

            return _fn

        REGISTRY.register(spec, _make(spec.id))
        registered.append(spec.id)
    return registered
