import pytest

import core.ai.ai_router as ai_router


@pytest.mark.integration
def test_live_delegate_architecture_design_routes_to_gemini():
    result = ai_router.delegate("Design an application architecture for a todo list app")

    assert result["provider"] == "gemini"
    assert result["task_type"] == "planning"
    assert result["response"]


@pytest.mark.integration
def test_live_delegate_log_analysis_routes_to_groq():
    result = ai_router.delegate("Analyze Docker error log: container exited with code 137")

    assert result["provider"] == "groq"
    assert result["task_type"] == "log_analysis"
    assert result["response"]


@pytest.mark.integration
def test_live_dashboard_reflects_real_quota_after_calls():
    ai_router.delegate("Analyze Docker error log")

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["groq"]["status"] == "connected"
    assert dashboard["groq"]["percent_remaining"] is not None
    assert 0 <= dashboard["groq"]["percent_remaining"] <= 100
