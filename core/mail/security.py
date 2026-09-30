"""core.mail security — sender authentication + link safety.

Email is untrusted data. This module validates *provenance* before anything is
acted on:

* SPF / DKIM / DMARC results parsed from the message headers;
* domain alignment of the sender against the provider's ``official_domain``;
* link safety: only the provider's own host (or a subdomain) is safe; punycode /
  non-ASCII (homoglyph) hosts, lookalikes/typosquats, mismatched redirectors and
  non-http(s) schemes are refused; known shorteners require expansion.

Nothing here performs network I/O — following a safe link is handed to the
Browser Operator (never a raw agent HTTP GET).
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Union

from core.browser.security import host_of, is_same_site
from core.mail.schema import (
    LinkRef,
    LinkVerdict,
    LinkVerdictResult,
    NormalizedEmail,
    SenderAuth,
    SenderAuthResult,
)

#: Well-known link shorteners — a direct follow is refused pending expansion.
SAFE_SHORTENERS = frozenset({
    "bit.ly", "t.co", "goo.gl", "tinyurl.com", "ow.ly", "buff.ly",
    "lnkd.in", "is.gd", "rb.gy", "cutt.ly", "shorturl.at", "t.ly",
})

_AUTH_RE = {
    "spf": re.compile(r"\bspf\s*=\s*([a-z]+)", re.IGNORECASE),
    "dkim": re.compile(r"\bdkim\s*=\s*([a-z]+)", re.IGNORECASE),
    "dmarc": re.compile(r"\bdmarc\s*=\s*([a-z]+)", re.IGNORECASE),
}
_SPF_RE = re.compile(r"^\s*([a-z]+)", re.IGNORECASE)

_AUTH_KEYWORDS = (
    "authentication-results", "arc-authentication-results",
    "received-spf", "dkim-signature",
)


def _to_sender_auth(token: Optional[str]) -> SenderAuth:
    if not token:
        return SenderAuth.NONE
    token = token.strip().lower()
    if token in SenderAuth._value2member_map_:
        return SenderAuth(token)
    if token.startswith("pass"):
        return SenderAuth.PASS
    if token.startswith("fail"):
        return SenderAuth.FAIL
    if token.startswith("softfail"):
        return SenderAuth.SOFTFAIL
    return SenderAuth.UNKNOWN


def parse_auth_results(headers: dict) -> SenderAuthResult:
    """Parse SPF/DKIM/DMARC verdicts from Authentication-Results / Received-SPF."""
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    ars = lowered.get("authentication-results") or lowered.get("arc-authentication-results") or ""

    spf = dkim = dmarc = None
    if ars:
        spf = _AUTH_RE["spf"].search(ars)
        dkim = _AUTH_RE["dkim"].search(ars)
        dmarc = _AUTH_RE["dmarc"].search(ars)

    result = SenderAuthResult(
        spf=_to_sender_auth(spf.group(1) if spf else None),
        dkim=_to_sender_auth(dkim.group(1) if dkim else None),
        dmarc=_to_sender_auth(dmarc.group(1) if dmarc else None),
    )

    if result.spf == SenderAuth.NONE and lowered.get("received-spf"):
        match = _SPF_RE.match(str(lowered["received-spf"]))
        result.spf = _to_sender_auth(match.group(1) if match else None)

    # A DKIM-Signature header present without an explicit verdict is evidence.
    if result.dkim == SenderAuth.NONE and lowered.get("dkim-signature"):
        result.notes.append("dkim-signature present (verdict not asserted)")
    return result


def _domain_of(address: Optional[str]) -> str:
    address = (address or "").strip()
    if "@" not in address:
        return ""
    return address.rsplit("@", 1)[-1].strip("<>").lower()


def _as_domains(official_domains: Union[str, Iterable[str], None]) -> list[str]:
    if not official_domains:
        return []
    if isinstance(official_domains, str):
        return [official_domains.lower().strip(".")]
    return [str(d).lower().strip(".") for d in official_domains if d]


def validate_sender(
    email: NormalizedEmail, official_domains: Union[str, Iterable[str], None]
) -> SenderAuthResult:
    domains = _as_domains(official_domains)
    result = parse_auth_results(email.auth_headers or {})
    result.official_domains = domains
    from_domain = _domain_of(email.from_address)
    result.from_domain = from_domain
    result.aligned = bool(from_domain) and any(is_same_site(from_domain, d) for d in domains)

    failures = (SenderAuth.FAIL, SenderAuth.SOFTFAIL, SenderAuth.PERMERROR)
    if not result.aligned:
        result.notes.append(
            f"sender domain mismatch: {from_domain or '(none)'!r} not aligned with {domains or '(none)'}"
        )
    for name, value in (("spf", result.spf), ("dkim", result.dkim), ("dmarc", result.dmarc)):
        if value in failures:
            result.notes.append(f"{name} authentication failure ({value.value})")
    return result


def is_suspicious_sender(result: SenderAuthResult) -> bool:
    return (not result.aligned) or result.fail_count > 0


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _lookalike(host: str, domains: Iterable[str]) -> bool:
    bare = host[4:] if host.startswith("www.") else host
    for d in domains:
        if bare == d:
            continue
        if _levenshtein(bare, d) <= 2:
            return True
        if bare.replace("-", "") == d.replace("-", ""):
            return True
    return False


def validate_link(
    url: str, official_domains: Union[str, Iterable[str], None]
) -> LinkVerdictResult:
    domains = _as_domains(official_domains)
    parsed = url.split("://", 1)
    scheme = parsed[0].lower() if len(parsed) == 2 else ""
    host = host_of(url)

    def verdict(v: LinkVerdict, reason: str, shortener: bool = False) -> LinkVerdictResult:
        return LinkVerdictResult(url=url, host=host, verdict=v, reason=reason,
                                 is_shortener=shortener, safe=(v == LinkVerdict.SAFE))

    if scheme not in ("http", "https"):
        return verdict(LinkVerdict.REFUSED, f"unsafe scheme: {scheme or '(none)'!r}")
    if not host:
        return verdict(LinkVerdict.REFUSED, "url has no host")

    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        return verdict(LinkVerdict.REFUSED, "non-ascii (homoglyph) host")
    if host.startswith("xn--") or ".xn--" in host:
        return verdict(LinkVerdict.REFUSED, "punycode/IDN host")

    if any(is_same_site(host, d) for d in domains):
        return verdict(LinkVerdict.SAFE, "provider host")
    if host in SAFE_SHORTENERS:
        return verdict(LinkVerdict.SUSPICIOUS, "link shortener requires expansion", shortener=True)
    if _lookalike(host, domains):
        return verdict(LinkVerdict.REFUSED, "lookalike/typosquat host")
    return verdict(LinkVerdict.REFUSED, "offsite host is not a provider domain")


def classify_links(email: NormalizedEmail, official_domains) -> NormalizedEmail:
    """Annotate every link on *email* with a verdict (returns a new model)."""
    annotated = [
        LinkRef(
            url=link.url, anchor=link.anchor, host=link.host or host_of(link.url),
            verdict=v.verdict, safe=v.safe,
        )
        for link in email.links
        for v in (validate_link(link.url, official_domains),)
    ]
    return email.model_copy(update={"links": annotated})


__all__ = [
    "SAFE_SHORTENERS",
    "parse_auth_results",
    "validate_sender",
    "is_suspicious_sender",
    "validate_link",
    "classify_links",
]
