from core.media_factory import rights


def test_gate_blocks_without_any_record():
    verdict = rights.evaluate_publishability([], [])
    assert verdict["publishable"] is False
    assert verdict["status"] == "BLOCKED"


def test_gate_blocks_record_without_evidence():
    verdict = rights.evaluate_publishability(
        [{"id": 1, "public_domain": True, "evidence": {}, "rights_status": "PUBLIC_DOMAIN"}]
    )
    assert verdict["publishable"] is False
    assert verdict["status"] == "BLOCKED"
    assert "evidence" in verdict["reason"]


def test_gate_allows_public_domain_with_evidence():
    verdict = rights.evaluate_publishability(
        [{"id": 2, "public_domain": True, "evidence": {"source": "archive.org"},
          "rights_status": "PUBLIC_DOMAIN"}]
    )
    assert verdict["publishable"] is True
    assert verdict["status"] == "VERIFIED"


def test_gate_allows_licensed_with_evidence():
    verdict = rights.evaluate_publishability(
        [{"id": 3, "public_domain": False, "evidence": {"contract": "LIC-9"},
          "rights_status": "LICENSED"}]
    )
    assert verdict["publishable"] is True


def test_gate_blocks_unknown_status_even_with_evidence():
    verdict = rights.evaluate_publishability(
        [{"id": 4, "public_domain": False, "evidence": {"note": "maybe"},
          "rights_status": "UNKNOWN"}]
    )
    assert verdict["publishable"] is False


def test_gate_allows_provenance_evidence_alone():
    verdict = rights.evaluate_publishability(
        [], [{"id": 9, "evidence": {"blockchain_tx": "0xabc"}}]
    )
    assert verdict["publishable"] is True
