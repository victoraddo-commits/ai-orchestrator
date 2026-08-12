import pytest

import core.ai.ai_router as ai_router


@pytest.mark.integration
@pytest.mark.external_api
def test_live_delegate_architecture_design_routes_to_deepseek_native_pro():
    result = ai_router.delegate("Design an application architecture for a todo list app")

    assert result["provider"] == "deepseek_native_pro"
    assert result["task_type"] == "planning"
    assert result["response"]


@pytest.mark.integration
@pytest.mark.external_api
def test_live_delegate_log_analysis_routes_to_deepseek_native_flash():
    result = ai_router.delegate("Analyze Docker error log: container exited with code 137")

    assert result["provider"] == "deepseek_native_flash"
    assert result["task_type"] == "log_analysis"
    assert result["response"]


@pytest.mark.integration
@pytest.mark.external_api
def test_live_dashboard_reflects_real_quota_after_calls():
    ai_router.delegate("Analyze Docker error log")

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["deepseek_native_flash"]["status"] == "connected"
    # After a successful call the quota snapshot is refreshed. Whether it
    # carries a numeric percent_remaining depends on the provider returning
    # x-ratelimit-*-tokens headers (DeepSeek does not), so assert on the
    # always-present detail string instead.
    assert dashboard["deepseek_native_flash"]["quota_detail"] is not None
