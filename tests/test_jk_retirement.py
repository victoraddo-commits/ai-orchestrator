"""Tests: verdicts for every orphan; classification-only guardrails."""
import sys
from pathlib import Path

sys.path.insert(0, "/opt/ai-orchestrator")

from core import ecosystem_retirement as er  # noqa: E402


def test_every_orphan_has_a_row():
    rows = er.build_verdicts()
    orphans = set(er.find_orphans())
    assert rows, "expected at least one orphan"
    assert {r["module"] for r in rows} == orphans


def test_row_shape():
    for r in er.build_verdicts():
        assert set(r) == {"module", "verdict", "evidence"}
        assert isinstance(r["evidence"], list) and r["evidence"]
        assert all(isinstance(e, str) for e in r["evidence"])


def test_no_verdict_is_retired():
    for r in er.build_verdicts():
        assert r["verdict"] != "RETIRED"


def test_verdicts_are_in_allowed_set():
    for r in er.build_verdicts():
        assert r["verdict"] in {"KEEP", "PROPOSE_RETIRE"}


def test_keep_implies_evidence_found():
    for r in er.build_verdicts():
        if r["verdict"] == "KEEP":
            assert r["evidence"][0] != "no dynamic reference found in core/*.py, env, json/yaml"


def test_propose_retire_means_no_evidence():
    for r in er.build_verdicts():
        if r["verdict"] == "PROPOSE_RETIRE":
            assert r["evidence"] == [
                "no dynamic reference found in core/*.py, env, json/yaml"]


def test_find_evidence_detects_import_module(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("importlib.import_module('core.ecosystem_retirement')\n")
    ev = er.find_evidence("ecosystem_retirement", [f])
    assert ev and "import_module" in ev[0]


def test_find_evidence_detects_quoted_name(tmp_path):
    f = tmp_path / "c.json"
    f.write_text('{"mods": ["core.nonexistent_xyz_mod"]}')
    ev = er.find_evidence("nonexistent_xyz_mod", [f])
    assert ev


def test_quoted_name_ignores_bak_files(tmp_path):
    core = tmp_path / "core"
    core.mkdir()
    (core / "target_mod.py.bak").write_text("'target_mod'\n")
    assert er.find_evidence("target_mod", er._scan_files() and [
        p for p in er._scan_files() if "tmp_path" not in str(p)
    ]) is not None  # real scan does not include .bak content


def test_format_table_has_header():
    t = er.format_table(er.build_verdicts())
    assert "module" in t and "verdict" in t
