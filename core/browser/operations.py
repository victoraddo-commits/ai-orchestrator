"""Operation validation for the browser operator.

Each operation has an explicit, typed contract. Validation is pure and runs
before the engine is touched, so an invalid or unsafe request never reaches
Chromium. ``evaluate`` and ``upload_file`` are guarded by explicit opt-in.
"""

from __future__ import annotations

from typing import Any, Optional

from core.browser.security import guard_evaluate, validate_navigation_url

# op -> {required: (...), optional: (...)}
OP_SPECS: dict[str, dict[str, tuple[str, ...]]] = {
    "navigate": {"required": ("url",), "optional": ("timeout_ms", "enforce_domain")},
    "inspect": {"required": (), "optional": ()},
    "fill": {"required": ("selector", "value"), "optional": ("credential",)},
    "click": {"required": ("selector",), "optional": ()},
    "screenshot": {"required": (), "optional": ("full_page", "mask_selectors")},
    "upload_file": {"required": ("selector", "path"), "optional": ("authorized",)},
    "evaluate": {"required": ("expression",), "optional": ("allow_evaluate",)},
    "wait_for": {"required": ("selector",), "optional": ("state", "timeout_ms")},
}


class InvalidOperation(ValueError):
    pass


def validate_operation(op: str, params: Optional[dict] = None) -> dict[str, Any]:
    params = dict(params or {})
    spec = OP_SPECS.get(op)
    if spec is None:
        raise InvalidOperation(f"unknown operation: {op!r}")

    for key in spec["required"]:
        if key not in params or params[key] in (None, ""):
            raise InvalidOperation(f"{op}: missing required parameter {key!r}")
    allowed = set(spec["required"]) | set(spec["optional"])
    extra = set(params) - allowed
    if extra:
        raise InvalidOperation(f"{op}: unexpected parameters {sorted(extra)}")

    if op == "navigate":
        params["url"] = validate_navigation_url(str(params["url"]))
        params.setdefault("timeout_ms", 30000)
        params.setdefault("enforce_domain", False)
    elif op == "fill":
        params["selector"] = _nonempty_str(params["selector"], "selector")
        params["value"] = str(params["value"])
        params["credential"] = bool(params.get("credential", False))
    elif op in ("click", "wait_for"):
        params["selector"] = _nonempty_str(params["selector"], "selector")
        if op == "wait_for":
            params.setdefault("state", "visible")
            params.setdefault("timeout_ms", 15000)
    elif op == "upload_file":
        params["selector"] = _nonempty_str(params["selector"], "selector")
        params["path"] = _nonempty_str(params["path"], "path")
        if not params.get("authorized"):
            raise InvalidOperation("upload_file requires authorized=True")
    elif op == "screenshot":
        params.setdefault("full_page", False)
        params.setdefault("mask_selectors", [])
    elif op == "evaluate":
        params["expression"] = guard_evaluate(
            str(params["expression"]), allow_evaluate=bool(params.get("allow_evaluate", False))
        )
    return params


def _nonempty_str(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise InvalidOperation(f"{field} must be a non-empty string")
    return text
