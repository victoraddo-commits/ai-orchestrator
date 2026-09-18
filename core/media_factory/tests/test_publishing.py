from core.media_factory import publishing


def test_dry_run_executes_without_publish_flag():
    decision = publishing.resolve_dispatch(
        "dry_run", publish_enabled=False, has_token=False, publishable=True)
    assert decision["action"] == "dry_run"
    assert decision["status"] == "VERIFIED"


def test_live_blocked_when_publish_disabled():
    decision = publishing.resolve_dispatch(
        "live", publish_enabled=False, has_token=True, publishable=True)
    assert decision["action"] == "blocked"
    assert decision["status"] == "BLOCKED"
    assert "MEDIA_PUBLISH_ENABLED" in decision["reason"]


def test_live_blocked_without_token():
    decision = publishing.resolve_dispatch(
        "live", publish_enabled=True, has_token=False, publishable=True)
    assert decision["action"] == "blocked"
    assert decision["status"] == "BLOCKED"
    assert "token" in decision["reason"].lower()


def test_test_and_canary_blocked_without_token():
    for mode in ("test", "canary"):
        decision = publishing.resolve_dispatch(
            mode, publish_enabled=True, has_token=False, publishable=True)
        assert decision["action"] == "blocked"


def test_gate_blocks_unpublishable_content():
    decision = publishing.resolve_dispatch(
        "dry_run", publish_enabled=True, has_token=True, publishable=False)
    assert decision["action"] == "blocked"
    assert "rights gate" in decision["reason"]


def test_unknown_mode_fails():
    decision = publishing.resolve_dispatch(
        "teleport", publish_enabled=True, has_token=True, publishable=True)
    assert decision["status"] == "FAILED"
