import subprocess

import pytest

import core.opencode_bridge as opencode_bridge


def _completed(stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["opencode"], returncode=returncode, stdout=stdout, stderr=stderr)


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


def test_run_coding_task_surfaces_stderr_on_top_level_cli_failure(monkeypatch):
    # Confirmed live 2026-07-30: a top-level CLI failure (auth/billing/
    # network -- the process exits non-zero before any tool_use event) used
    # to leave tool_errors empty, silently discarding the real reason (e.g.
    # a credit-exhaustion message) and leaving callers with nothing but the
    # generic "generation did not succeed" fallback.
    monkeypatch.setattr(
        opencode_bridge,
        "_run_opencode_process",
        lambda p, i, m, t: _completed("", returncode=1, stderr="Error: insufficient credit balance"),
    )

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["success"] is False
    assert result["tool_errors"] == [{"tool": None, "content": "Error: insufficient credit balance"}]


def test_run_coding_task_falls_back_to_stdout_when_stderr_empty(monkeypatch):
    monkeypatch.setattr(
        opencode_bridge,
        "_run_opencode_process",
        lambda p, i, m, t: _completed("some non-JSON diagnostic output", returncode=1, stderr=""),
    )

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["success"] is False
    assert result["tool_errors"] == [{"tool": None, "content": "some non-JSON diagnostic output"}]


def test_run_coding_task_leaves_tool_errors_empty_when_nothing_captured(monkeypatch):
    # Existing behavior preserved: a nonzero exit with genuinely nothing on
    # stderr/stdout still results in an empty tool_errors list, not a
    # fabricated message.
    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", lambda p, i, m, t: _completed("", returncode=1))

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["success"] is False
    assert result["tool_errors"] == []


def test_run_coding_task_does_not_overwrite_real_tool_errors_with_stderr(monkeypatch):
    # When tool_errors are already populated (a real tool failure was
    # parsed), stderr must not clobber that more specific signal.
    stdout = '{"type":"tool_use","part":{"tool":"bash","state":{"status":"error","error":"permission denied"}}}'
    monkeypatch.setattr(
        opencode_bridge,
        "_run_opencode_process",
        lambda p, i, m, t: _completed(stdout, returncode=1, stderr="some unrelated stderr noise"),
    )

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["tool_errors"] == [{"tool": "bash", "content": "permission denied"}]


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


# --- 13W: real per-call cost from step_finish events -------------------------

def test_run_coding_task_sums_cost_across_step_finish_events(monkeypatch):
    # Real shape confirmed live: step_finish events carry the actual billed
    # cost per step (e.g. cost: 0.0139422 via OpenRouter/Zen).
    stdout = "\n".join([
        '{"type":"step_finish","part":{"type":"step-finish","cost":0.00200562,"tokens":{"total":7913}}}',
        '{"type":"text","part":{"text":"Done."}}',
        '{"type":"step_finish","part":{"type":"step-finish","cost":0.0005844,"tokens":{"total":7998}}}',
    ])

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", lambda p, i, m, t: _completed(stdout))

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["cost"] == pytest.approx(0.00200562 + 0.0005844)


def test_run_coding_task_recognizes_the_hyphenated_step_finish_spelling(monkeypatch):
    # The CLI stream and the session store spell part types differently
    # (tool_use vs tool) -- accept both separators for step finish.
    stdout = '{"type":"step-finish","part":{"type":"step-finish","cost":0.0139422}}'

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", lambda p, i, m, t: _completed(stdout))

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["cost"] == pytest.approx(0.0139422)


def test_run_coding_task_reports_null_cost_when_no_step_reports_one(monkeypatch):
    # Never fabricate: a run whose events carry no cost figure records None,
    # not an estimate from token counts.
    stdout = "\n".join([
        '{"type":"text","part":{"text":"Done."}}',
        '{"type":"step_finish","part":{"type":"step-finish","tokens":{"total":100}}}',
    ])

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", lambda p, i, m, t: _completed(stdout))

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["cost"] is None


def test_run_coding_task_reports_null_cost_on_timeout(monkeypatch):
    def fake_run(project_path, instruction, model, timeout):
        raise subprocess.TimeoutExpired(cmd="opencode", timeout=timeout)

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", fake_run)

    result = opencode_bridge.run_coding_task("/proj", "do something", timeout=5)

    assert result["cost"] is None


def test_run_coding_task_ignores_non_numeric_cost_values(monkeypatch):
    stdout = '{"type":"step_finish","part":{"type":"step-finish","cost":"unknown"}}'

    monkeypatch.setattr(opencode_bridge, "_run_opencode_process", lambda p, i, m, t: _completed(stdout))

    result = opencode_bridge.run_coding_task("/proj", "do something")

    assert result["cost"] is None


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
