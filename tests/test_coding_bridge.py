import pytest

import core.coding_bridge as bridge


def test_load_bridge_api_key_raises_clear_error_when_not_provisioned(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "API_KEY_PATH", tmp_path / "does-not-exist")

    with pytest.raises(bridge.CloudCLINotConfigured):
        bridge._load_bridge_api_key()


def test_load_bridge_api_key_reads_provisioned_key(tmp_path, monkeypatch):
    key_path = tmp_path / "cloudcli_api_key"
    key_path.write_text("ck_abc123")
    monkeypatch.setattr(bridge, "API_KEY_PATH", key_path)

    assert bridge._load_bridge_api_key() == "ck_abc123"


@pytest.mark.parametrize("line,expected", [
    ('data: {"kind": "text", "text": "hi"}', {"kind": "text", "text": "hi"}),
    ("", None),
    ("event: message", None),
    (": keep-alive comment", None),
    ("data: not-json", None),
])
def test_parse_sse_data_line(line, expected):
    assert bridge._parse_sse_data_line(line) == expected


def test_run_coding_task_happy_path_reports_file_and_commit(monkeypatch):
    events = [
        {"kind": "session_created", "sessionId": "sess-1"},
        {"kind": "text", "text": "Adding a health check endpoint."},
        {"kind": "tool_use", "toolName": "Write", "toolInput": {"file_path": "app/health.py"}},
        {"kind": "tool_result", "toolName": "Write", "isError": False, "content": "ok"},
        {"kind": "tool_use", "toolName": "Bash", "toolInput": {"command": "git commit -m 'add health check'"}},
        {"kind": "tool_result", "toolName": "Bash", "isError": False, "content": "ok"},
        {"kind": "complete", "exitCode": 0, "success": True, "aborted": False},
    ]

    monkeypatch.setattr(bridge, "_stream_agent_events", lambda **kwargs: iter(events))

    # before/after git HEAD returned by two sequential calls within run_coding_task
    calls = {"n": 0}

    def fake_git_head(path):
        calls["n"] += 1
        return "before-sha" if calls["n"] == 1 else "after-sha"

    monkeypatch.setattr(bridge, "_git_head", fake_git_head)
    monkeypatch.setattr(
        bridge,
        "_git_commits_between",
        lambda path, before, after: [{"sha": "after-sha", "message": "add health check"}],
    )
    monkeypatch.setattr(bridge, "_git_uncommitted_files", lambda path: [])

    result = bridge.run_coding_task("/proj", "Add a health check endpoint")

    assert result["success"] is True
    assert result["aborted"] is False
    assert result["session_id"] == "sess-1"
    assert "health check" in result["response_text"]
    assert "app/health.py" in result["files_changed"]
    assert result["commits"] == [{"sha": "after-sha", "message": "add health check"}]
    assert result["tool_errors"] == []


def test_run_coding_task_surfaces_tool_errors(monkeypatch):
    events = [
        {"kind": "session_created", "sessionId": "sess-2"},
        {"kind": "tool_use", "toolName": "Bash", "toolInput": {"command": "pytest"}},
        {"kind": "tool_result", "toolName": "Bash", "isError": True, "content": "2 failed, 3 passed"},
        {"kind": "complete", "exitCode": 0, "success": False, "aborted": False},
    ]

    monkeypatch.setattr(bridge, "_stream_agent_events", lambda **kwargs: iter(events))
    monkeypatch.setattr(bridge, "_git_head", lambda path: "same-sha")
    monkeypatch.setattr(bridge, "_git_commits_between", lambda path, before, after: [])
    monkeypatch.setattr(bridge, "_git_uncommitted_files", lambda path: ["app/broken.py"])

    result = bridge.run_coding_task("/proj", "Fix the failing test")

    assert result["success"] is False
    assert len(result["tool_errors"]) == 1
    assert result["tool_errors"][0]["tool"] == "Bash"
    assert "app/broken.py" in result["files_changed"]


def test_run_coding_task_surfaces_top_level_error_events(monkeypatch):
    # e.g. the Claude Code CLI process itself exits non-zero (crash, bad
    # permission mode, hitting max turns) -- distinct from a tool_result
    # error, and easy to silently drop if only tool_result is handled.
    events = [
        {"kind": "session_created", "sessionId": "sess-4"},
        {"kind": "error", "content": "Claude Code process exited with code 1"},
        {"kind": "complete", "exitCode": 1, "success": False, "aborted": False},
    ]

    monkeypatch.setattr(bridge, "_stream_agent_events", lambda **kwargs: iter(events))
    monkeypatch.setattr(bridge, "_git_head", lambda path: None)
    monkeypatch.setattr(bridge, "_git_commits_between", lambda path, before, after: [])
    monkeypatch.setattr(bridge, "_git_uncommitted_files", lambda path: [])

    result = bridge.run_coding_task("/proj", "Do something that will fail")

    assert result["success"] is False
    assert len(result["tool_errors"]) == 1
    assert result["tool_errors"][0]["tool"] is None
    assert "exited with code 1" in result["tool_errors"][0]["content"]


def test_run_coding_task_reports_aborted_session(monkeypatch):
    events = [
        {"kind": "session_created", "sessionId": "sess-3"},
        {"kind": "complete", "exitCode": 0, "success": False, "aborted": True},
    ]

    monkeypatch.setattr(bridge, "_stream_agent_events", lambda **kwargs: iter(events))
    monkeypatch.setattr(bridge, "_git_head", lambda path: None)
    monkeypatch.setattr(bridge, "_git_commits_between", lambda path, before, after: [])
    monkeypatch.setattr(bridge, "_git_uncommitted_files", lambda path: [])

    result = bridge.run_coding_task("/proj", "Do something long-running")

    assert result["aborted"] is True
    assert result["commits"] == []


def test_run_coding_task_handles_non_git_directory_without_crashing(monkeypatch):
    events = [
        {"kind": "complete", "exitCode": 0, "success": True, "aborted": False},
    ]

    monkeypatch.setattr(bridge, "_stream_agent_events", lambda **kwargs: iter(events))
    monkeypatch.setattr(bridge, "_git_head", lambda path: None)
    monkeypatch.setattr(bridge, "_git_commits_between", lambda path, before, after: [])
    monkeypatch.setattr(bridge, "_git_uncommitted_files", lambda path: [])

    result = bridge.run_coding_task("/not-a-repo", "Write a README")

    assert result["success"] is True
    assert result["commits"] == []
    assert result["files_changed"] == []


def test_run_coding_task_deduplicates_repeated_file_writes(monkeypatch):
    events = [
        {"kind": "tool_use", "toolName": "Edit", "toolInput": {"file_path": "app/main.py"}},
        {"kind": "tool_use", "toolName": "Edit", "toolInput": {"file_path": "app/main.py"}},
        {"kind": "complete", "exitCode": 0, "success": True, "aborted": False},
    ]

    monkeypatch.setattr(bridge, "_stream_agent_events", lambda **kwargs: iter(events))
    monkeypatch.setattr(bridge, "_git_head", lambda path: None)
    monkeypatch.setattr(bridge, "_git_commits_between", lambda path, before, after: [])
    monkeypatch.setattr(bridge, "_git_uncommitted_files", lambda path: ["app/main.py"])

    result = bridge.run_coding_task("/proj", "Tweak main.py twice")

    assert result["files_changed"] == ["app/main.py"]
