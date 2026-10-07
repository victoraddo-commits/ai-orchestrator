"""core.mail security — sender auth + link safety (email is untrusted data)."""

from core.mail.parser import parse_raw_email
from core.mail.security import (
    SenderAuth,
    LinkVerdict,
    parse_auth_results,
    validate_sender,
    validate_link,
)

from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "mail"


def _email(name: str):
    return parse_raw_email((FIX / name).read_bytes())


def test_auth_results_pass_parsed():
    result = parse_auth_results(
        {"Authentication-Results": "mx.example.org; spf=pass smtp.mailfrom=proton.me; "
         "dkim=pass header.d=proton.me; dmarc=pass header.from=proton.me"}
    )
    assert result.spf == SenderAuth.PASS
    assert result.dkim == SenderAuth.PASS
    assert result.dmarc == SenderAuth.PASS


def test_auth_results_from_received_spf_when_no_ars():
    result = parse_auth_results(
        {"Received-SPF": "fail (google.com: domain of evil.example does not designate 1.2.3.4 as permitted sender)"}
    )
    assert result.spf == SenderAuth.FAIL


def test_sender_aligned_pass_not_suspicious():
    email = _email("verification_proton.eml")
    result = validate_sender(email, "proton.me")
    assert result.aligned is True
    assert "suspicious" not in " ".join(result.notes).lower()


def test_sender_domain_mismatch_is_suspicious():
    email = _email("phishing_sender.eml")
    result = validate_sender(email, "proton.me")
    assert result.aligned is False
    assert any("mismatch" in n.lower() for n in result.notes)


def test_sender_auth_fail_is_suspicious():
    email = _email("phishing_sender.eml")
    result = validate_sender(email, "proton.me")
    assert result.spf == SenderAuth.FAIL
    assert result.dmarc == SenderAuth.FAIL
    assert any("fail" in n.lower() for n in result.notes)


def test_link_legit_provider_host_ok():
    v = validate_link("https://account.proton.me/verify?token=abc", "proton.me")
    assert v.verdict == LinkVerdict.SAFE
    assert v.safe is True


def test_link_offsite_mismatched_redirector_refused():
    v = validate_link("https://account.proton.me.attacker.example/verify", "proton.me")
    assert v.verdict == LinkVerdict.REFUSED


def test_link_typosquat_refused():
    v = validate_link("https://protonn.me/verify", "proton.me")
    assert v.verdict == LinkVerdict.REFUSED


def test_link_punycode_refused():
    v = validate_link("https://xn--protn-9ta.me/verify", "proton.me")
    assert v.verdict == LinkVerdict.REFUSED


def test_link_unsafe_scheme_refused():
    v = validate_link("javascript:alert(1)", "proton.me")
    assert v.verdict == LinkVerdict.REFUSED


def test_link_shortener_requires_expansion():
    v = validate_link("https://bit.ly/abc123", "proton.me")
    assert v.verdict == LinkVerdict.SUSPICIOUS
    assert v.is_shortener is True
    assert v.safe is False
