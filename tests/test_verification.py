from core.verification import verify_service, load_verification_history


def test_verify_service_has_unified_lifecycle_fields():
    result = verify_service("some-service-with-no-findings", trace_id="inc1")

    assert len(result["id"]) == 8
    assert result["trace_id"] == "inc1"
    assert result["status"] in ("resolved", "unresolved")
    assert result["history"][0]["status"] == "verifying"


def test_verify_service_resolved_when_no_remaining_findings():
    result = verify_service("some-service-with-no-findings", trace_id="inc1")

    assert result["status"] == "resolved"
    assert result["remaining_findings"] == []


def test_verify_service_persists_to_history_file():
    verify_service("svc-a", trace_id="inc1")
    verify_service("svc-b", trace_id="inc2")

    history = load_verification_history()
    assert len(history) == 2
