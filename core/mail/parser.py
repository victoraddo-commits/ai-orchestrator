"""core.mail parser — RFC822 -> NormalizedEmail (stdlib only).

Email is untrusted data. This module:

* parses headers/bodies with the stdlib :mod:`email` package;
* sanitizes HTML with a strict allow-list (scripts, styles, forms, frames and
  *remote content* are stripped) so raw HTML is never rendered unvalidated;
* extracts links with their anchor + host;
* records attachment **metadata only** — never opens one;
* hashes the raw headers for provenance;
* guesses the provider from the sender domain against the known-domain map.
"""

from __future__ import annotations

import hashlib
import html as _html
import re
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime, parseaddr
from html.parser import HTMLParser
from typing import Iterable, Optional
from urllib.parse import urlparse

from core.browser.security import host_of, sanitize_untrusted_text
from core.id_generator import generate_id
from core.mail.schema import (
    AttachmentMeta,
    LinkRef,
    MailDirection,
    NormalizedEmail,
    TrustLevel,
    now_iso,
)

# sender domain -> provider id (used only as a *guess*)
PROVIDER_DOMAINS: dict[str, str] = {
    "proton.me": "proton",
    "protonmail.com": "proton",
    "pm.me": "proton",
    "gmail.com": "google",
    "googlemail.com": "google",
    "outlook.com": "microsoft",
    "amazon.com": "amazon",
    "github.com": "github",
}

_BARE_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

# headers kept (verbatim) for later sender-authentication validation
_AUTH_HEADER_KEYS = (
    "authentication-results", "arc-authentication-results",
    "received-spf", "dkim-signature",
)


# ---------------------------------------------------------------------------
# HTML sanitization (strict allow-list)
# ---------------------------------------------------------------------------

_ALLOWED_TAGS = {
    "a", "p", "br", "b", "i", "em", "strong", "u", "s", "span", "div",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
    "table", "thead", "tbody", "tr", "td", "th", "code", "pre", "hr",
}
_VOID_ALLOWED = {"br", "hr"}
# tags whose *content* is dropped entirely
_DROP_CONTENT = {"script", "style", "head", "title", "iframe", "object",
                 "embed", "svg", "math", "form", "noscript", "template"}
# tags dropped (tag removed, text preserved) incl. all remote content
_DROP_TAG = {"img", "picture", "source", "video", "audio", "link", "meta",
             "input", "button", "select", "textarea", "canvas", "map", "area"}
_ALLOWED_ATTRS = {"href", "title", "alt"}
_SAFE_SCHEMES = ("http", "https", "mailto")


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.warnings: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self._skip_depth += 1
            self.warnings.append(f"dropped <{tag}> (and content)")
            return
        if tag in _DROP_TAG:
            if tag in ("img", "source", "picture", "video", "audio", "link"):
                self.warnings.append(f"blocked remote content <{tag}>")
            else:
                self.warnings.append(f"dropped <{tag}>")
            return
        if tag not in _ALLOWED_TAGS:
            self.warnings.append(f"dropped <{tag}>")
            return
        rendered = self._render_attrs(tag, attrs)
        if tag in _VOID_ALLOWED:
            self.out.append(f"<{tag}{rendered}>")
        else:
            self.out.append(f"<{tag}{rendered}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _ALLOWED_TAGS and tag not in _VOID_ALLOWED:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._skip_depth:
            return
        self.out.append(_html.escape(data))

    def _render_attrs(self, tag, attrs) -> str:
        rendered = []
        for key, value in attrs:
            key = key.lower()
            if key.startswith("on") or key in ("style", "src", "srcset",
                                               "background", "formaction"):
                self.warnings.append(f"stripped attribute {key!r}")
                continue
            if key not in _ALLOWED_ATTRS:
                continue
            if key == "href" and value:
                scheme = urlparse(value).scheme.lower()
                if scheme not in _SAFE_SCHEMES:
                    self.warnings.append(f"stripped unsafe href scheme {scheme!r}")
                    continue
            if value is not None:
                rendered.append(f' {key}="{_html.escape(value, quote=True)}"')
        return "".join(rendered)


def sanitize_html(raw_html: str) -> tuple[str, list[str]]:
    """Return (sanitized_html, warnings). Pure; no network."""
    if not raw_html:
        return "", []
    s = _Sanitizer()
    try:
        s.feed(raw_html)
        s.close()
    except Exception:  # never let a malformed message crash the worker
        return _html.escape(raw_html), ["parser error: fell back to escaped text"]
    return "".join(s.out), s.warnings


# ---------------------------------------------------------------------------
# link extraction
# ---------------------------------------------------------------------------

class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: Optional[str] = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href.strip(), "".join(self._text).strip()))
            self._href, self._text = None, []


def extract_links(text: str, html: str = "") -> list[LinkRef]:
    found: list[tuple[str, str]] = []
    if html:
        c = _AnchorCollector()
        try:
            c.feed(html)
            c.close()
        except Exception:
            pass
        found.extend(c.links)
    if text:
        found.extend((url, "") for url in _BARE_URL_RE.findall(text))
    seen: set[str] = set()
    links: list[LinkRef] = []
    for url, anchor in found:
        if url in seen:
            continue
        seen.add(url)
        links.append(LinkRef(url=url, anchor=anchor, host=host_of(url)))
    return links


# ---------------------------------------------------------------------------
# message parsing
# ---------------------------------------------------------------------------

def _decode(part) -> str:
    try:
        content = part.get_content()
        return content if isinstance(content, str) else str(content)
    except Exception:
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return str(payload or "")


def _header_map(msg) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in msg.items():
        out.setdefault(key.lower(), value)
    return out


def _guess_provider(*addresses: Optional[str]) -> Optional[str]:
    for addr in addresses:
        if not addr:
            continue
        _, email = parseaddr(addr)
        domain = (email.split("@")[-1] if "@" in email else "").lower()
        if domain in PROVIDER_DOMAINS:
            return PROVIDER_DOMAINS[domain]
    return None


def parse_raw_email(raw, direction: MailDirection = MailDirection.INBOUND,
                    email_id: Optional[str] = None) -> NormalizedEmail:
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="replace")
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    headers = _header_map(msg)

    from_address = parseaddr(msg.get("From", ""))[1] or msg.get("From", "") or ""
    from_display = parseaddr(msg.get("From", ""))[0]
    reply_to = parseaddr(msg.get("Reply-To", ""))[1] or None
    return_path = parseaddr(msg.get("Return-Path", ""))[1] or None
    to = [addr for _, addr in getaddresses(msg.get_all("To", [])) if addr]
    cc = [addr for _, addr in getaddresses(msg.get_all("Cc", [])) if addr]

    subject = str(msg.get("Subject", "") or "")
    message_id = (msg.get("Message-ID") or None)
    thread = (msg.get("References") or msg.get("In-Reply-To") or None)
    if thread:
        thread = thread.split()[0]

    body_text_parts: list[str] = []
    body_html_parts: list[str] = []
    attachments: list[AttachmentMeta] = []

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if disposition == "attachment" or filename:
            payload = part.get_payload(decode=True) or b""
            attachments.append(AttachmentMeta(
                filename=filename or "unnamed",
                content_type=content_type,
                size=len(payload),
                disposition=disposition or "attachment",
                opened=False,
                flagged_for_review=True,
            ))
            continue
        if content_type == "text/plain":
            body_text_parts.append(_decode(part))
        elif content_type == "text/html":
            body_html_parts.append(_decode(part))

    body_text_raw = "\n".join(p for p in body_text_parts if p)
    body_text, _ = sanitize_untrusted_text(body_text_raw)
    html_sanitized, _ = sanitize_html("\n".join(body_html_parts))

    links = extract_links(body_text_raw, "\n".join(body_html_parts))

    date_iso = None
    if msg.get("Date"):
        try:
            date_iso = parsedate_to_datetime(msg["Date"]).isoformat()
        except Exception:
            date_iso = str(msg["Date"])

    header_blob = "\n".join(f"{k}: {v}" for k, v in sorted(msg.items()))
    raw_headers_hash = hashlib.sha256(header_blob.encode("utf-8", "replace")).hexdigest()

    provider = _guess_provider(from_address, return_path, reply_to)
    auth_headers = {k: v for k, v in headers.items() if k in _AUTH_HEADER_KEYS}

    return NormalizedEmail(
        email_id=email_id or f"mail-{generate_id()}",
        message_id=message_id,
        thread_id=thread,
        direction=direction,
        from_address=from_address,
        from_display=from_display,
        reply_to=reply_to,
        return_path=return_path,
        to=to,
        cc=cc,
        subject=subject,
        date=date_iso,
        body_text=body_text,
        body_html_sanitized=html_sanitized,
        links=links,
        attachments=attachments,
        raw_headers_hash=raw_headers_hash,
        provider_guessed=provider,
        trust=TrustLevel.UNTRUSTED,
        received_at=now_iso(),
        auth_headers=auth_headers,
    )


__all__ = ["parse_raw_email", "sanitize_html", "extract_links", "PROVIDER_DOMAINS"]
