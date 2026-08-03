from core.orchestrator_cycle import run_cycle
from core import build_manager
from core import roadmap_manager


def test_run_cycle_completes_without_error_and_has_expected_shape():
    result = run_cycle()

    assert set(result) == {
        "state", "findings", "incidents", "decisions", "builds", "roadmap_progress", "remediation", "verification"
    }
    assert isinstance(result["incidents"], list)
    assert isinstance(result["decisions"], list)
    assert isinstance(result["builds"], list)
    assert result["roadmap_progress"]["action"] == "disabled"
    assert isinstance(result["remediation"], list)
    assert isinstance(result["verification"], list)


def test_run_cycle_processes_a_roadmap_created_build_in_the_same_cycle(monkeypatch, tmp_path):
    # Real gap found live: advance_roadmap() runs *after* advance_builds()
    # within run_cycle(), so a build it creates this cycle previously sat
    # at REQUESTED, untouched, until the *next* scheduled cycle -- a full
    # extra INTERVAL of latency stacked on top of the wait for this cycle
    # to fire at all. A build created by the roadmap step should be worked
    # on the same cycle it's created, not the next one.
    import core.roadmap_engine as roadmap_engine

    roadmap_path = tmp_path / "roadmap.json"
    roadmap_path.write_text(
        '{"schema_version": 1, "phases": [{"id": "X", "name": "n", "description": "d", '
        '"status": "pending", "dependencies": [], "priority": 1}]}'
    )
    monkeypatch.setattr(roadmap_engine, "ROADMAP_PATH", roadmap_path)
    # advance_roadmap() creates self-targeting builds in an isolated clone
    # (see core.roadmap_manager._create_isolated_self_clone) rather than
    # operating on SELF_PROJECT_PATH directly -- must not be the real
    # ai-orchestrator repo during a test, or this would do a real `git
    # checkout` against this actual working directory (exactly the bug
    # class fixed live tonight). Stub the clone step entirely here since
    # this test is about same-cycle build processing, not the clone itself
    # (that's covered by tests/test_roadmap_manager.py).
    fake_clone_dir = tmp_path / "isolated-clone"
    fake_clone_dir.mkdir()
    monkeypatch.setattr(roadmap_manager, "_create_isolated_self_clone", lambda **kwargs: str(fake_clone_dir))
    roadmap_manager.enable_autonomous_mode()

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning", "duration_ms": 10,
            "response": "Plan: do the thing that was requested.",
        },
    )

    result = run_cycle()

    assert result["roadmap_progress"]["action"] == "started_phase"
    assert len(result["builds"]) == 1
    assert result["builds"][0]["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"


def test_run_cycle_advances_pending_builds(monkeypatch):
    build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning", "duration_ms": 10,
            "response": "Plan: FastAPI + SQLite.",
        },
    )

    result = run_cycle()

    assert len(result["builds"]) == 1
    assert result["builds"][0]["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"


def test_run_cycle_records_sent_message_id_for_state_change_builds(monkeypatch):
    # After a state-change notification is actually sent, the cycle must
    # remember which build that Telegram message announced
    # (record_sent_build_message) -- that link is what lets the operator
    # answer via native reply-to when several builds are pending at once.
    import core.telegram_bridge as telegram_bridge

    build = build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning", "duration_ms": 10,
            "response": "Plan: FastAPI + SQLite.",
        },
    )

    monkeypatch.setattr(
        telegram_bridge,
        "send_message",
        lambda text: {"ok": True, "result": {"message_id": 4242}},
    )

    recorded = []
    monkeypatch.setattr(
        telegram_bridge,
        "record_sent_build_message",
        lambda message_id, build_id: recorded.append((message_id, build_id)),
    )

    result = run_cycle()

    assert result["builds"][0]["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"
    assert (4242, build["id"]) in recorded


def test_run_cycle_skips_recording_when_send_fails(monkeypatch):
    # A failed send returns no message_id -- there is nothing for the
    # operator to reply to, so no mapping must be written.
    import core.telegram_bridge as telegram_bridge

    build_manager.create_build("todo-app", "Build a todo app", "/tmp/proj")

    monkeypatch.setattr(
        build_manager,
        "delegate",
        lambda description, **kwargs: {
            "provider": "gemini", "task_type": "planning", "duration_ms": 10,
            "response": "Plan: FastAPI + SQLite.",
        },
    )

    def failing_send(text):
        raise RuntimeError("Telegram sendMessage failed: ConnectionError")

    monkeypatch.setattr(telegram_bridge, "send_message", failing_send)

    recorded = []
    monkeypatch.setattr(
        telegram_bridge,
        "record_sent_build_message",
        lambda message_id, build_id: recorded.append((message_id, build_id)),
    )

    result = run_cycle()

    assert result["builds"][0]["status"] == "WAITING_FOR_ARCHITECTURE_APPROVAL"
    assert recorded == []


def test_run_cycle_incidents_have_unified_lifecycle_fields():
    result = run_cycle()

    for incident in result["incidents"]:
        assert "trace_id" in incident
        assert "history" in incident
        assert incident["status"] in (
            "open", "investigating", "approved", "executing",
            "verifying", "resolved", "failed", "closed",
        )
