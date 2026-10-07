"""core.mail parser — TDD suite (offline fixtures, no network)."""

from pathlib import Path

import pytest

from core.mail.parser import parse_raw_email, sanitize_html, extract_links

FIX = Path(__file__).parent / "fixtures" / "mail"


def _raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_parse_verification_normalized_model():
    email = parse_raw_email(_raw("verification_proton.eml"))
    assert email.message_id == "<verif-001@proton.me>"
    assert email.thread_id == "<signup-000@proton.me>"
    assert email.from_address == "no-reply@proton.me"
    assert email.from_display == "Proton Mail"
    assert email.reply_to == "support@proton.me"
    assert email.to == ["Kai-Enzoai@protonmail.com"]
    assert email.subject == "Verify your email address"
    assert "verify your email" in email.body_text.lower()
    assert email.direction == "inbound"
    assert email.trust == "untrusted"
    assert email.raw_headers_hash and len(email.raw_headers_hash) == 64
    assert email.provider_guessed == "proton"
    assert email.received_at


def test_html_sanitization_strips_scripts_and_remote_content():
    email = parse_raw_email(_raw("verification_proton.eml"))
    html = email.body_html_sanitized.lower()
    assert "<script" not in html
    assert "alert(" not in html
    assert "<style" not in html
    assert "tracker.evil.example" not in html
    assert "verify your email" in html  # safe text/anchor survives


def test_sanitize_html_removes_on_attributes_and_unsafe_schemes():
    html, warnings = sanitize_html(
        '<p onclick="evil()">hi</p>'
        '<a href="javascript:alert(1)">x</a>'
        '<a href="https://good.example/p">ok</a>'
        '<img src="http://remote.example/x.png">'
    )
    assert "onclick" not in html
    assert "javascript:" not in html
    assert "https://good.example/p" in html
    assert "remote.example" not in html
    assert warnings  # something was stripped


def test_extract_links_dedup_and_host():
    links = extract_links(
        "see https://a.example/x and https://a.example/x again",
        '<a href="https://b.example/y">B</a>',
    )
    urls = [l.url for l in links]
    assert urls.count("https://a.example/x") == 1
    assert any(l.host == "b.example" and l.anchor == "B" for l in links)


def test_attachments_flagged_not_opened():
    email = parse_raw_email(_raw("with_attachment.eml"))
    assert len(email.attachments) == 1
    att = email.attachments[0]
    assert att.filename == "statement.pdf"
    assert att.content_type == "application/pdf"
    assert att.opened is False
    assert att.flagged_for_review is True
    assert att.size > 0


def test_parser_rejects_secret_looking_fields():
    with pytest.raises(Exception):
        from core.mail.schema import NormalizedEmail

        NormalizedEmail(
            email_id="e1", from_address="a@b.com", password="hunter2",
            received_at="now",
        )
