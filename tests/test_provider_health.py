import core.ai.provider_health as provider_health


def test_record_and_get_quota_snapshot_roundtrip():
    provider_health.record_quota_snapshot("groq", status="ok", percent_remaining=85.3)

    snapshot = provider_health.get_quota_snapshot("groq")

    assert snapshot["status"] == "ok"
    assert snapshot["percent_remaining"] == 85.3
    assert "checked_at" in snapshot


def test_get_quota_snapshot_returns_none_when_never_recorded():
    assert provider_health.get_quota_snapshot("gemini") is None


def test_get_all_quota_snapshots_covers_every_recorded_provider():
    provider_health.record_quota_snapshot("groq", status="ok", percent_remaining=90)
    provider_health.record_quota_snapshot("qwen4_text", status="quota_exceeded", percent_remaining=0)

    snapshots = provider_health.get_all_quota_snapshots()

    assert set(snapshots) == {"groq", "qwen4_text"}


def test_prune_stale_snapshots_removes_unregistered_providers():
    provider_health.record_quota_snapshot("ghost", status="error", detail="stale")
    provider_health.record_quota_snapshot("kai_coder", status="ok", percent_remaining=50)

    removed = provider_health.prune_stale_snapshots({"kai_coder"})

    assert removed == ["ghost"]
    assert provider_health.get_quota_snapshot("ghost") is None
    assert provider_health.get_quota_snapshot("kai_coder")["status"] == "ok"


def test_prune_stale_snapshots_is_a_noop_when_all_registered():
    provider_health.record_quota_snapshot("kai_coder", status="ok")

    assert provider_health.prune_stale_snapshots({"kai_coder"}) == []
    assert provider_health.get_quota_snapshot("kai_coder")["status"] == "ok"


def test_capture_from_groq_headers_computes_percent_from_tokens():
    headers = {
        "x-ratelimit-remaining-tokens": "11962",
        "x-ratelimit-limit-tokens": "12000",
        "x-ratelimit-remaining-requests": "998",
        "x-ratelimit-limit-requests": "1000",
    }

    snapshot = provider_health.capture_from_response_headers("groq", headers)

    assert snapshot["status"] == "ok"
    assert snapshot["percent_remaining"] == round(11962 / 12000 * 100, 1)
    assert snapshot["remaining_tokens"] == 11962
    assert snapshot["limit_tokens"] == 12000


def test_capture_from_headers_with_no_ratelimit_data_reports_no_data():
    snapshot = provider_health.capture_from_response_headers("gemini", {})

    assert snapshot["status"] == "ok"
    assert snapshot["percent_remaining"] is None
    assert "no quota data" in snapshot["detail"].lower()


def test_capture_quota_exceeded_from_error_records_zero_percent():
    snapshot = provider_health.capture_quota_exceeded("qwen4_text", detail="insufficient_quota: billing required")

    assert snapshot["status"] == "quota_exceeded"
    assert snapshot["percent_remaining"] == 0
    assert "billing" in snapshot["detail"].lower()


def test_capture_provider_error_records_raw_detail_without_classifying_it():
    snapshot = provider_health.capture_provider_error(
        "claude", detail="Claude usage limit reached. Your limit will reset at 3pm."
    )

    assert snapshot["status"] == "error"
    assert snapshot["percent_remaining"] is None
    assert "usage limit reached" in snapshot["detail"].lower()
