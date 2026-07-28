import pytest

import core.kai.planner as planner
from core.lifecycle import InvalidTransition
from core.ai.ai_router import AllProvidersFailed


def _stub_signals(monkeypatch):
    monkeypatch.setattr(planner, "load_roadmap", lambda: {"phases": []})
    monkeypatch.setattr(planner, "get_progress_summary", lambda: {"total": 0})
    monkeypatch.setattr(planner, "analyze_health", lambda: [])
    monkeypatch.setattr(planner, "get_build_history", lambda: [])
    monkeypatch.setattr(planner, "get_usage_history", lambda: [])
    monkeypatch.setattr(planner.provider_health, "get_all_quota_snapshots", lambda: {})


def _stub_delegate(monkeypatch, response, provider="gemini"):
    monkeypatch.setattr(
        planner,
        "delegate",
        lambda description, **kwargs: {"provider": provider, "task_type": "planning", "response": response, "duration_ms": 5},
    )


def test_generate_proposal_parses_json_array_and_persists_proposed_objects(monkeypatch):
    _stub_signals(monkeypatch)
    _stub_delegate(monkeypatch, """[
        {"title": "Fix flaky template", "description": "desc", "suggested_action": "do x",
         "rationale": "because y", "source_signals": ["build_failures:fastapi"],
         "target_roadmap_phase_draft": null}
    ]""")

    result = planner.generate_proposal()

    assert result["status"] == "ok"
    assert result["provider"] == "gemini"
    assert len(result["created"]) == 1

    created = result["created"][0]
    assert created["status"] == "proposed"
    assert created["title"] == "Fix flaky template"
    assert created["synthesized_by"] == "gemini"
    assert "id" in created

    stored = planner.load_proposals()
    assert len(stored) == 1
    assert stored[0]["id"] == created["id"]


def test_generate_proposal_handles_json_wrapped_in_markdown_fence(monkeypatch):
    _stub_signals(monkeypatch)
    _stub_delegate(monkeypatch, '```json\n[{"title": "T", "description": "D"}]\n```')

    result = planner.generate_proposal()

    assert len(result["created"]) == 1
    assert result["created"][0]["title"] == "T"


def test_generate_proposal_returns_no_created_proposals_when_ai_proposes_none(monkeypatch):
    _stub_signals(monkeypatch)
    _stub_delegate(monkeypatch, "[]")

    result = planner.generate_proposal()

    assert result["status"] == "ok"
    assert result["created"] == []
    assert planner.load_proposals() == []


def test_generate_proposal_falls_back_to_a_single_wrapped_proposal_on_unparseable_response(monkeypatch):
    _stub_signals(monkeypatch)
    _stub_delegate(monkeypatch, "Sorry, I cannot produce JSON right now, here is prose instead.")

    result = planner.generate_proposal()

    assert result["status"] == "ok"
    assert len(result["created"]) == 1
    assert result["created"][0]["description"] == "Sorry, I cannot produce JSON right now, here is prose instead."


def test_generate_proposal_reports_error_status_when_every_provider_fails(monkeypatch):
    _stub_signals(monkeypatch)

    def boom(description, **kwargs):
        raise AllProvidersFailed("no provider available")

    monkeypatch.setattr(planner, "delegate", boom)

    result = planner.generate_proposal()

    assert result["status"] == "error"
    assert result["created"] == []
    assert planner.load_proposals() == []


def test_generate_proposal_never_calls_add_phase(monkeypatch):
    _stub_signals(monkeypatch)
    _stub_delegate(monkeypatch, '[{"title": "T", "description": "D"}]')

    def fail_add_phase(*args, **kwargs):
        raise AssertionError("generate_proposal() must never call add_phase() directly")

    monkeypatch.setattr(planner, "add_phase", fail_add_phase)

    planner.generate_proposal()


def test_list_proposals_filters_by_status(monkeypatch):
    _stub_signals(monkeypatch)
    _stub_delegate(monkeypatch, '[{"title": "A", "description": "d"}, {"title": "B", "description": "d"}]')
    planner.generate_proposal()

    proposals = planner.list_proposals()
    planner.update_proposal_status(proposals[0]["id"], "under_review")

    assert len(planner.list_proposals(status="proposed")) == 1
    assert len(planner.list_proposals(status="under_review")) == 1
    assert len(planner.list_proposals()) == 2


def test_update_proposal_status_walks_the_full_lifecycle(monkeypatch):
    _stub_signals(monkeypatch)
    _stub_delegate(monkeypatch, '[{"title": "A", "description": "d"}]')
    proposal = planner.generate_proposal()["created"][0]

    planner.update_proposal_status(proposal["id"], "under_review")
    planner.update_proposal_status(proposal["id"], "approved")
    updated = planner.update_proposal_status(proposal["id"], "implemented")

    assert updated["status"] == "implemented"
    assert [h["status"] for h in updated["history"]] == ["proposed", "under_review", "approved", "implemented"]


def test_update_proposal_status_rejects_invalid_transition(monkeypatch):
    _stub_signals(monkeypatch)
    _stub_delegate(monkeypatch, '[{"title": "A", "description": "d"}]')
    proposal = planner.generate_proposal()["created"][0]

    with pytest.raises(InvalidTransition):
        planner.update_proposal_status(proposal["id"], "approved")


def test_update_proposal_status_raises_for_unknown_id():
    with pytest.raises(ValueError):
        planner.update_proposal_status("does-not-exist", "under_review")


def test_promote_proposal_requires_approved_status(monkeypatch):
    _stub_signals(monkeypatch)
    _stub_delegate(monkeypatch, '[{"title": "A", "description": "d"}]')
    proposal = planner.generate_proposal()["created"][0]

    with pytest.raises(InvalidTransition):
        planner.promote_proposal(proposal["id"], "13E")


def test_promote_proposal_creates_a_proposed_status_roadmap_phase_and_marks_implemented(monkeypatch):
    _stub_signals(monkeypatch)
    _stub_delegate(monkeypatch, """[{
        "title": "Add caching layer", "description": "desc",
        "target_roadmap_phase_draft": {"name": "Add caching", "description": "desc",
                                        "dependencies": [], "priority": 42}
    }]""")
    proposal = planner.generate_proposal()["created"][0]

    planner.update_proposal_status(proposal["id"], "under_review")
    planner.update_proposal_status(proposal["id"], "approved")

    created_phases = []
    monkeypatch.setattr(
        planner,
        "add_phase",
        lambda **kwargs: created_phases.append(kwargs) or {"id": kwargs["id"], "status": "proposed", **kwargs},
    )

    result = planner.promote_proposal(proposal["id"], "13E")

    assert created_phases[0]["id"] == "13E"
    assert created_phases[0]["name"] == "Add caching"
    assert created_phases[0]["priority"] == 42
    assert "status" not in created_phases[0]

    assert result["proposal"]["status"] == "implemented"
    assert result["proposal"]["roadmap_phase_id"] == "13E"
