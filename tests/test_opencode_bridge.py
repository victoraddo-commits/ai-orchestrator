import subprocess

import core.opencode_bridge as opencode_bridge


def _completed(stdout, returncode=0):
    return subprocess.CompletedProcess(args=["opencode"], returncode=returncode, stdout=stdout, stderr="")


def test_run_coding_task_reports_file_write_and_commit(monkeypatch):
    stdout = "\n".join([
        '{"type":"tool_use","part":{"tool":"write","state":{"status":"completed","input":{"filePath":"/proj/hello.txt"}}}}',
        '{"type":"tool_use","part":{"tool":"bash","state":{"status":"completed","input":{"command":"git commit -m x"},"output":"[main abc123] x\\n"}}}',
        '{"type":"text","part":{"text":"Done."}}',
    ])

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", lambda p, i, m, t: _completed(stdout))

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["success"] is True
    assert result["response_text"] == "Done."
    assert result["files_changed"] == ["/proj/hello.txt"]
    assert len(result["commits"]) == 1
    assert "abc123" in result["commits"][0]["message"]


def test_run_coding_task_uses_the_default_model(monkeypatch):
    captured = {}

    def fake_run(project_path, instruction, model, timeout):
        captured["model"] = model
        return _completed("")

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", fake_run)

    opencode_bridge.run_coding_task("/proj", "do something")

    assert captured["model"] == opencode_bridge.OPENCODE_DEFAULT_MODEL
    assert "minimax" not in opencode_bridge.OPENCODE_DEFAULT_MODEL


def test_run_coding_task_surfaces_tool_errors_and_marks_unsuccessful(monkeypatch):
    stdout = '{"type":"tool_use","part":{"tool":"bash","state":{"status":"error","error":"permission denied"}}}'

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", lambda p, i, m, t: _completed(stdout))

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["success"] is False
    assert result["tool_errors"] == [{"tool": "bash", "content": "permission denied"}]


def test_run_coding_task_marks_unsuccessful_on_nonzero_exit_code(monkeypatch):
    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", lambda p, i, m, t: _completed("", returncode=1))

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["success"] is False


def test_run_coding_task_deduplicates_repeated_file_writes(monkeypatch):
    stdout = "\n".join([
        '{"type":"tool_use","part":{"tool":"write","state":{"status":"completed","input":{"filePath":"/proj/a.py"}}}}',
        '{"type":"tool_use","part":{"tool":"write","state":{"status":"completed","input":{"filePath":"/proj/a.py"}}}}',
    ])

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", lambda p, i, m, t: _completed(stdout))

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["files_changed"] == ["/proj/a.py"]


def test_run_coding_task_ignores_malformed_json_lines(monkeypatch):
    stdout = "\n".join([
        "not json at all",
        '{"type":"text","part":{"text":"still works"}}',
    ])

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", lambda p, i, m, t: _completed(stdout))

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["success"] is True
    assert result["response_text"] == "still works"


def test_run_coding_task_reports_timeout_as_failure_not_a_crash(monkeypatch):
    def fake_run(project_path, instruction, model, timeout):
        raise subprocess.TimeoutExpired(cmd="opencode", timeout=timeout)

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", fake_run)

    result = opencode_bridge.run_coding_task("/proj", "do something", timeout=5)

    assert result["success"] is False
    assert "5s" in result["tool_errors"][0]["content"]


def test_run_coding_task_passes_project_path_and_instruction_through(monkeypatch):
    captured = {}

    def fake_run(project_path, instruction, model, timeout):
        captured["project_path"] = project_path
        captured["instruction"] = instruction
        return _completed("")

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", fake_run)

    opencode_bridge.run_coding_task("/some/proj", "build a widget")

    assert captured["project_path"] == "/some/proj"
    assert captured["instruction"] == "build a widget"
