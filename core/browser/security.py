"""Browser security controls (pure, side-effect free).

Implements the STEP 3 security requirements:

* never type credentials into an unexpected domain — validate the *current* URL
  host against the provider descriptor's ``official_domain`` immediately before
  any credential field is filled;
* block redirects to non-provider domains while credentials are in play;
* allow only http/https navigation (no ``file:`` / ``javascript:`` / ``data:``);
* never let page content become agent instructions (sanitize + mark untrusted);
* redact secrets from any text that leaves the browser (console, screenshots
  metadata, audit details);
* guard ``evaluate`` against network / exfiltration primitives.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

# --- errors -------------------------------------------------------------------


class UnsafeUrl(ValueError):
    """Navigation target scheme/host is not allowed."""


class DomainMismatch(ValueError):
    """A URL host does not match the expected provider domain."""


class CredentialTargetRefused(DomainMismatch):
    """Refused to enter credentials because the active host is not the provider."""


class RedirectBlocked(DomainMismatch):
    """Navigation landed on a non-provider host while credentials are in play."""


class EvaluateRefused(ValueError):
    """The evaluate expression uses a disallowed (network/exfil) primitive."""


# --- secret-key detection (mirrors core.secret_guard) -------------------------

SECRET_KEY_MARKERS = (
    "password", "passwd", "passphrase", "secret", "token",
    "api_key", "apikey", "private_key", "credential", "otp",
    "totp_seed", "seed_phrase", "mnemonic", "cookie", "authorization",
)


def find_secret_keys(payload: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            kl = str(key).lower()
            here = f"{path}.{key}" if path else str(key)
            if any(m in kl for m in SECRET_KEY_MARKERS):
                found.append(here)
            found.extend(find_secret_keys(value, here))
    elif isinstance(payload, (list, tuple)):
        for i, value in enumerate(payload):
            found.extend(find_secret_keys(value, f"{path}[{i}]"))
    return found


def assert_no_secret_keys(payload: Any) -> None:
    found = find_secret_keys(payload)
    if found:
        raise ValueError("refusing to persist secret-looking keys: " + ", ".join(found))


# --- URL / domain validation --------------------------------------------------

_ALLOWED_SCHEMES = ("http", "https")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def validate_navigation_url(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrl(f"navigation scheme not allowed: {parsed.scheme or '(none)'!r}")
    if not parsed.hostname:
        raise UnsafeUrl(f"navigation url has no host: {url!r}")
    return url


def is_same_site(host: str, official_domain: str) -> bool:
    """True when *host* is the official domain or a subdomain of it."""
    host = (host or "").lower().strip(".")
    official = (official_domain or "").lower().strip(".")
    if not host or not official:
        return False
    return host == official or host.endswith("." + official)


def assert_credential_target(url: str, official_domain: Optional[str]) -> None:
    """THE blocking check before any credential field is filled.

    Raises :class:`CredentialTargetRefused` unless the current host is the
    provider's official domain (or a subdomain). Considers only http/https.
    """
    parsed = urlparse(url or "")
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise CredentialTargetRefused(
            f"credential entry refused: active scheme {parsed.scheme!r} is not http/https")
    host = host_of(url)
    if not official_domain:
        raise CredentialTargetRefused(
            "credential entry refused: provider has no official_domain to validate against")
    if not is_same_site(host, official_domain):
        raise CredentialTargetRefused(
            f"credential entry refused: host {host!r} is not {official_domain!r} or a subdomain")


def assert_navigation_stays_on_site(url: str, official_domain: Optional[str]) -> None:
    """Post-navigation guard for when credentials are in play (redirect block)."""
    if not official_domain:
        return
    if not is_same_site(host_of(url), official_domain):
        raise RedirectBlocked(
            f"redirect blocked: landed on {host_of(url)!r}, not the provider domain "
            f"{official_domain!r}")


# --- content handling ---------------------------------------------------------

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_TEXT = 8000


def sanitize_untrusted_text(text: str, max_len: int = _MAX_TEXT) -> tuple[str, bool]:
    """Strip control chars, collapse whitespace, cap length.

    Returns (clean_text, truncated). Callers must keep the ``untrusted`` flag.
    """
    if not text:
        return "", False
    clean = _CTRL_RE.sub("", text.replace("\r", "\n"))
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    truncated = len(clean) > max_len
    return clean[:max_len], truncated


def redact_text(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of each secret value with ``***``."""
    out = text or ""
    for secret in secrets:
        if secret and len(str(secret)) >= 3:
            out = out.replace(str(secret), "***")
    return out


# --- evaluate guard -----------------------------------------------------------

_EVALUATE_DENY = (
    "fetch(", "xmlhttprequest", "websocket", "import(", "require(",
    "eval(", "function(", "=>", "document.cookie", "localstorage",
    "sessionstorage", "indexeddb", "navigator.sendbeacon", "postmessage",
    "window.open", "location.assign", "location.replace", "location.href",
    "frames[", "iframe",
)


def is_evaluate_allowed(expression: str) -> tuple[bool, str]:
    expr = (expression or "").strip()
    if not expr:
        return False, "empty expression"
    if len(expr) > 2000:
        return False, "expression too long"
    lowered = expr.lower()
    for token in _EVALUATE_DENY:
        if token in lowered:
            return False, f"disallowed primitive: {token!r}"
    return True, ""


def guard_evaluate(expression: str, *, allow_evaluate: bool) -> str:
    if not allow_evaluate:
        raise EvaluateRefused("evaluate requires explicit allow_evaluate=True")
    ok, reason = is_evaluate_allowed(expression)
    if not ok:
        raise EvaluateRefused(f"evaluate refused: {reason}")
    return expression
