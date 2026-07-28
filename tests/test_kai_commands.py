import core.kai.commands as commands
import core.memory as memory


def test_analyze_system_health_matches_and_aggregates_health_and_provider_status(monkeypatch):
    monkeypatch.setattr(commands, "analyze_health", lambda: [{"severity": "warning", "issue": "x"}])
    monkeypatch.setattr(
        commands.provider_health, "get_all_quota_snapshots", lambda: {"claude": {"status": "ok"}}
    )

    result = commands.dispatch("Kai, analyze system health")

    assert result["matched"] is True
    assert result["error"] is None
    assert result["result"]["findings"] == [{"severity": "warning", "issue": "x"}]
    assert result["result"]["provider_health"] == {"claude": {"status": "ok"}}


def test_analyze_system_health_is_case_insensitive_and_tolerates_trailing_period(monkeypatch):
    monkeypatch.setattr(commands, "analyze_health", lambda: [])
    monkeypatch.setattr(commands.provider_health, "get_all_quota_snapshots", lambda: {})

    assert commands.dispatch("kai, ANALYZE system health.")["matched"] is True


def test_find_improvements_falls_back_gracefully_when_13c_planner_is_absent():
    result = commands.dispatch("Kai, find improvements")

    assert result["matched"] is True
    assert result["error"] is None
    assert result["result"]["status"] == "pending_13c"


def test_create_an_improvement_proposal_matches_the_same_handler():
    result = commands.dispatch("Kai, create an improvement proposal")

    assert result["matched"] is True
    assert result["result"]["status"] == "pending_13c"


def test_continue_roadmap_advances_the_roadmap(monkeypatch):
    monkeypatch.setattr(commands, "advance_roadmap", lambda: {"action": "nothing_to_do"})

    result = commands.dispatch("Kai, continue roadmap")

    assert result["matched"] is True
    assert result["result"] == {"action": "nothing_to_do"}


def test_review_recent_failures_filters_to_failed_and_rolled_back(tmp_path):
    memory.save(
        "build_history.json",
        [
            {"build_id": "1", "status": "COMPLETED"},
            {"build_id": "2", "status": "FAILED"},
            {"build_id": "3", "status": "ROLLED_BACK"},
        ],
    )

    result = commands.dispatch("Kai, review recent failures")

    assert result["matched"] is True
    ids = {entry["build_id"] for entry in result["result"]}
    assert ids == {"2", "3"}


def test_prepare_the_next_engineering_phase_fetches_the_next_phase(monkeypatch):
    monkeypatch.setattr(commands, "get_next_phase", lambda: {"id": "13D"})

    result = commands.dispatch("Kai, prepare the next engineering phase")

    assert result["matched"] is True
    assert result["result"] == {"id": "13D"}


def test_unmatched_phrase_returns_matched_false():
    result = commands.dispatch("Kai, do a barrel roll")

    assert result["matched"] is False
    assert result["result"] is None
    assert result["error"] is not None


def test_dispatch_reports_handler_exceptions_instead_of_raising(monkeypatch):
    def _boom():
        raise RuntimeError("roadmap store is corrupt")

    monkeypatch.setattr(commands, "advance_roadmap", _boom)

    result = commands.dispatch("Kai, continue roadmap")

    assert result["matched"] is True
    assert result["result"] is None
    assert result["error"] == "roadmap store is corrupt"
