#!/usr/bin/env bash
# Kai orchestrator regression gate.
#
# Runs the test suite and fails ONLY if a test that is not in the known-bad
# baseline fails (a NEW failure). Known ENV/STALE/REAL failures and explicitly
# quarantined FLAKY tests never fail the gate. When a baselined test starts
# passing it is reported as a NEW PASS (informational, does not fail unless
# GATE_STRICT_PASSES=1).
#
# Design rules (see tests/BASELINE.md):
#   * deterministic: baseline is a fixed list, not a "last run" heuristic.
#   * honest: flaky tests are quarantined by name/module, visibly flagged in the
#     output, never silently dropped.
#   * no secrets in logs: only pytest node ids and counts are printed, never
#     tracebacks or assertion reprs.
#
# Usage:
#   scripts/test_regression_gate.sh                 # run full suite, gate
#   scripts/test_regression_gate.sh tests/foo.py    # gate a subset
#   GATE_SHOW_REASONS=1 scripts/test_regression_gate.sh   # print one-line reasons for NEW failures
#   GATE_STRICT_PASSES=1 scripts/test_regression_gate.sh  # also fail on NEW passes
#
# Exit: 0 = no new failures; 1 = new failure(s); 2 = gate/infra error.

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PYTHON="${PYTHON:-${REPO_ROOT}/.venv/bin/python}"
BASELINE="${BASELINE:-tests/baseline_failures.txt}"
FLAKY="${FLAKY:-tests/baseline_flaky.txt}"
OUT_DIR="${OUT_DIR:-${TMPDIR:-/tmp}/kai-regression-gate}"
mkdir -p "${OUT_DIR}"
JUNIT="${OUT_DIR}/junit.xml"
PYTEST_LOG="${OUT_DIR}/pytest.log"

TEST_ARGS=("$@")
FULL_SUITE=0
if [ "${#TEST_ARGS[@]}" -eq 0 ]; then
  TEST_ARGS=("tests/")
  FULL_SUITE=1
fi

if [ ! -x "${PYTHON}" ]; then
  echo "ERROR: python not found at ${PYTHON} (set PYTHON=/path/to/python)" >&2
  exit 2
fi
if [ ! -f "${BASELINE}" ] || [ ! -f "${FLAKY}" ]; then
  echo "ERROR: missing ${BASELINE} or ${FLAKY}; cannot gate." >&2
  exit 2
fi

echo "== Kai regression gate =="
echo "python : ${PYTHON}"
echo "target : ${TEST_ARGS[*]}"
echo "baseline: ${BASELINE}"
echo "flaky  : ${FLAKY}"
echo "junit  : ${JUNIT}"
echo

# --tb=no keeps assertion reprs (which may contain secrets) out of logs/junit.
# -q keeps output small; junit carries the machine-readable result.
set +e
"${PYTHON}" -m pytest -q -p no:cacheprovider --tb=no \
  --junitxml="${JUNIT}" "${TEST_ARGS[@]}" >"${PYTEST_LOG}" 2>&1
PYTEST_RC=$?
set -e

echo "pytest exit code: ${PYTEST_RC}"
tail -n 1 "${PYTEST_LOG}" | grep -E 'passed|failed|error' || true
echo

set +e
GATE_SHOW_REASONS="${GATE_SHOW_REASONS:-0}" \
GATE_STRICT_PASSES="${GATE_STRICT_PASSES:-0}" \
GATE_FULL_SUITE="${FULL_SUITE}" \
"${PYTHON}" - "${JUNIT}" "${BASELINE}" "${FLAKY}" <<'PY'
import os
import sys
import xml.etree.ElementTree as ET

junit, baseline_path, flaky_path = sys.argv[1], sys.argv[2], sys.argv[3]
show_reasons = os.environ.get("GATE_SHOW_REASONS", "0") == "1"
strict_passes = os.environ.get("GATE_STRICT_PASSES", "0") == "1"
full_suite = os.environ.get("GATE_FULL_SUITE", "0") == "1"


def to_node(classname, name):
    parts = classname.split(".")
    idx = 0
    for i, p in enumerate(parts):
        if p[:1].isupper():
            idx = i
            break
    else:
        idx = len(parts)
    path = "/".join(parts[:idx]) + ".py"
    cls = ".".join(parts[idx:])
    return f"{path}::{cls + '::' if cls else ''}{name}"


baseline = {}
with open(baseline_path) as f:
    for line in f:
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        bits = line.split("\t")
        if len(bits) >= 2:
            baseline[bits[1]] = (bits[0], bits[2] if len(bits) > 2 else "")

flaky_exact = {}
flaky_modules = {}
with open(flaky_path) as f:
    for line in f:
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        bits = line.split("\t")
        if len(bits) >= 2 and bits[0] == "MODULE":
            flaky_modules[bits[1]] = bits[2] if len(bits) > 2 else ""
        elif len(bits) >= 2 and bits[0] == "FLAKY":
            flaky_exact[bits[1]] = bits[2] if len(bits) > 2 else ""


def is_flaky(nid):
    if nid in flaky_exact:
        return True
    for mod in flaky_modules:
        if nid.startswith(mod + "::"):
            return True
    return False


try:
    root = ET.parse(junit).getroot()
except Exception as e:  # noqa: BLE001
    print(f"GATE ERROR: cannot parse junit {junit}: {e}")
    sys.exit(2)

passed, failed, errored, skipped = [], [], [], []
for tc in root.iter("testcase"):
    nid = to_node(tc.get("classname", ""), tc.get("name", ""))
    if any(c.tag == "failure" for c in tc):
        failed.append((nid, next(c.get("message", "") for c in tc if c.tag == "failure")))
    elif any(c.tag == "error" for c in tc):
        errored.append((nid, next(c.get("message", "") for c in tc if c.tag == "error")))
    elif any(c.tag == "skipped" for c in tc):
        skipped.append(nid)
    else:
        passed.append(nid)

failing = {n: m for n, m in (failed + errored)}

new_failures = []
known_failures = []
flaky_failed = []
for nid, msg in failing.items():
    if is_flaky(nid):
        flaky_failed.append((nid, msg))
    elif nid in baseline:
        known_failures.append(nid)
    else:
        new_failures.append((nid, msg))

new_passes = []
for nid in passed:
    if nid in baseline and not is_flaky(nid):
        new_passes.append(nid)

missing = [n for n in baseline if n not in failing and n not in passed and n not in skipped]

print("---- gate summary ----")
print(f"passed           : {len(passed)}")
print(f"failed/errored   : {len(failing)}")
print(f"skipped          : {len(skipped)}")
print(f"known failures   : {len(known_failures)}  (in baseline; ignored)")
print(f"quarantined flaky: {len(flaky_failed)} failed this run (ignored, visible)")
print(f"NEW failures     : {len(new_failures)}")
print(f"NEW passes       : {len(new_passes)}  (baselined tests now passing)")
if full_suite:
    print(f"baseline missing : {len(missing)}  (test id no longer collected)")

if flaky_failed:
    print("\n[quarantined flaky failures this run]")
    for nid, _ in sorted(flaky_failed):
        print(f"  FLAKY  {nid}")

if new_passes:
    print("\n[NEW passes — update the baseline to lock these in]")
    for nid in sorted(new_passes):
        print(f"  PASS   {nid}")

if missing and full_suite:
    print("\n[baseline entries not collected this run — stale baseline?]")
    for nid in sorted(missing):
        print(f"  GONE   {nid}")

if new_failures:
    print("\n[NEW FAILURES — not in baseline]")
    for nid, msg in sorted(new_failures):
        line = f"  FAIL   {nid}"
        if show_reasons and msg:
            line += f"  ::  {msg.strip().splitlines()[0][:200]}"
        print(line)

print()
rc = 0
if new_failures:
    rc = 1
if strict_passes and new_passes:
    rc = 1
if rc == 0:
    print("GATE: PASS — no new failures" + ("" if not new_passes else f" ({len(new_passes)} new pass(es) reported)"))
else:
    print("GATE: FAIL — see NEW FAILURES above")
sys.exit(rc)
PY
GATE_RC=$?
set -e

exit ${GATE_RC}
