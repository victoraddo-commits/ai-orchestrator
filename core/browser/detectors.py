"""Page-state detectors (pure heuristics over a sanitized snapshot).

Detectors reason only over the ``PageSnapshot`` (accessible text + element
refs), never raw HTML. They are intentionally simple, keyword/DOM based, and
fully unit-testable with fixtures — no browser required.
"""

from __future__ import annotations

from typing import Any

_AUTH_LOGGED_OUT = (
    "sign in", "sign-in", "log in", "login", "password", "forgot password",
    "create account", "register",
)
_AUTH_LOGGED_IN = (
    "log out", "logout", "sign out", "sign-out", "my account", "dashboard",
    "profile", "settings", "welcome back",
)
_CAPTCHA = ("captcha", "recaptcha", "hcaptcha", "not a robot", "are you human")
_MFA = (
    "verification code", "two-factor", "two factor", "2fa", "authenticator",
    "one-time code", "one time code", "enter the code", "security code",
    "authenticator app", "6-digit",
)
_VERIFY = (
    "verify your email", "verify your phone", "check your email", "check your inbox",
    "verification link", "confirm your email", "we sent", "we've sent",
    "activate your account", "confirm your number", "verification email",
)
_ERROR = (
    "invalid", "incorrect", "try again", "something went wrong", "error",
    "failed", "not recognized", "couldn't", "could not",
)


def _norm(snapshot: Any) -> dict:
    if hasattr(snapshot, "model_dump"):
        snapshot = snapshot.model_dump()
    return dict(snapshot or {})


def _elements(snapshot: dict) -> list[dict]:
    out = []
    for el in snapshot.get("elements") or []:
        if hasattr(el, "model_dump"):
            el = el.model_dump()
        out.append(dict(el or {}))
    return out


def _haystack(snapshot: dict) -> str:
    parts = [str(snapshot.get("title") or ""), str(snapshot.get("text") or "")]
    for el in _elements(snapshot):
        parts.append(str(el.get("name") or ""))
        parts.append(str(el.get("role") or ""))
        parts.append(str(el.get("type") or ""))
    return " ".join(parts).lower()


def _has(snapshot: dict, keywords: tuple[str, ...]) -> list[str]:
    hay = _haystack(snapshot)
    return [k for k in keywords if k in hay]


def detect_auth_state(snapshot: Any) -> str:
    """Return 'logged_in' | 'logged_out' | 'unknown'."""
    snap = _norm(snapshot)
    if snap.get("has_password_field"):
        return "logged_out"
    out_hits = _has(snap, _AUTH_LOGGED_OUT)
    in_hits = _has(snap, _AUTH_LOGGED_IN)
    # A logout control / dashboard is the strongest logged-in signal.
    if in_hits and not out_hits:
        return "logged_in"
    if out_hits and not in_hits:
        return "logged_out"
    if in_hits and out_hits:
        # Prefer the explicit logout signal when present.
        return "logged_in" if any("log out" in h or "logout" in h or "sign out" in h for h in in_hits) else "unknown"
    return "unknown"


def detect_captcha(snapshot: Any) -> dict:
    snap = _norm(snapshot)
    hits = _has(snap, _CAPTCHA)
    for el in _elements(snap):
        blob = f"{el.get('selector','')} {el.get('name','')} {el.get('role','')}".lower()
        if "captcha" in blob or "recaptcha" in blob or "hcaptcha" in blob:
            hits.append("element:captcha")
    return {"detected": bool(hits), "evidence": sorted(set(hits))}


def detect_mfa_prompt(snapshot: Any) -> dict:
    snap = _norm(snapshot)
    hits = _has(snap, _MFA)
    for el in _elements(snap):
        blob = f"{el.get('selector','')} {el.get('name','')} {el.get('type','')}".lower()
        if any(t in blob for t in ("otp", "one-time", "totp", "mfa", "2fa")) or (
            el.get("type") == "text" and "code" in blob
        ):
            hits.append("element:code-input")
    return {"detected": bool(hits), "evidence": sorted(set(hits))}


def detect_verification_page(snapshot: Any) -> dict:
    snap = _norm(snapshot)
    hits = _has(snap, _VERIFY)
    return {"detected": bool(hits), "evidence": sorted(set(hits))}


def detect_error(snapshot: Any) -> dict:
    snap = _norm(snapshot)
    hits: list[str] = []
    for el in _elements(snap):
        role = str(el.get("role") or "").lower()
        sel = str(el.get("selector") or "").lower()
        if role == "alert" or "error" in sel or "alert" in sel:
            hits.append(f"element:{el.get('name') or sel}")
    hits.extend(_has(snap, _ERROR))
    return {"detected": bool(hits), "evidence": sorted(set(hits))}


DETECTORS = {
    "auth_state": detect_auth_state,
    "captcha": detect_captcha,
    "mfa": detect_mfa_prompt,
    "verification": detect_verification_page,
    "error": detect_error,
}


def run_detectors(snapshot: Any) -> dict:
    return {name: fn(snapshot) for name, fn in DETECTORS.items()}
