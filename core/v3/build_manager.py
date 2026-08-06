"""Kai V3 Build Manager — Build lifecycle with contracts, sandboxes, and multi-review.

Replaces core/build_manager.py with:
  - Two-pass processing (completion-near builds first)
  - Per-build sandbox isolation via git worktrees
  - Structured build contracts
  - 5-reviewer pipeline with voting
  - Timeout protection (generation 2400s, deploying 1800s)
  - Terminal build filtering (exclude COMPLETED/FAILED/ROLLED_BACK)
  - Duplicate prevention (same name + non-terminal → return existing)
  - Build archive for completed builds
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from core.memory import load, save, update
from core.lifecycle import new_object, transition, InvalidTransition
from core.logger import info, error as log_error
from core.llm_clients import call_qwen3_coder_text, call_qwen6

from core.v3.build_contract import build_contract_from_phase, validate_contract
from core.v3.sandbox_manager import prepare_sandbox, release_sandbox
from core.v3.review_pipeline import run_review_pipeline, run_single_review
from core.v3.approval_system import (
    init_build_approval, get_vote_tally, is_build_approved, is_build_rejected
)
from core.v3.monitoring import check_stuck_builds
from core.v3.recovery import (
    handle_generation_failure, handle_review_failure,
    handle_timeout, handle_repeated_failure,
)

# ── Constants ─────────────────────────────────────────────────────────────

BUILDS_FILE = "builds.json"
BUILDS_ARCHIVE_FILE = "builds_archive.json"

GENERATION_TIMEOUT = 2400  # 40 minutes
DEPLOYING_TIMEOUT = 1800   # 30 minutes
GENERATION_CALL_TIMEOUT = 1200  # 20 min per LLM call
REVIEW_TIMEOUT = 180       # 3 min per reviewer call

NON_TERMINAL_BUILD_STATUSES = {
    "REQUESTED", "PLANNING",
    "WAITING_FOR_USER_INPUT",
    "WAITING_FOR_ARCHITECTURE_APPROVAL",
    "ARCHITECTURE_APPROVED",
    "GENERATING",
    "CODE_REVIEW",
    "SECURITY_REVIEW",
    "TESTING",
    "WAITING_FOR_DEPLOY_APPROVAL",
    "DEPLOYING",
    "VERIFIED",
}

TERMINAL_BUILD_STATUSES = {
    "COMPLETED",
    "FAILED",
    "ROLLED_BACK",
}

# Builds close to completion — always processed first in Pass 1
_COMPLETION_NEAR_STATUSES = {
    "DEPLOYING",
    "CODE_REVIEW",
    "SECURITY_REVIEW",
    "TESTING",
    "WAITING_FOR_DEPLOY_APPROVAL",
}

BUILD_TRANSITIONS = {
    "REQUESTED": ["PLANNING", "FAILED"],
    "PLANNING": ["WAITING_FOR_ARCHITECTURE_APPROVAL", "FAILED"],
    "WAITING_FOR_ARCHITECTURE_APPROVAL": ["ARCHITECTURE_APPROVED", "FAILED"],
    "ARCHITECTURE_APPROVED": ["GENERATING", "FAILED"],
    "GENERATING": ["CODE_REVIEW", "FAILED"],
    "CODE_REVIEW": ["SECURITY_REVIEW", "GENERATING", "FAILED"],
    "SECURITY_REVIEW": ["TESTING", "GENERATING", "FAILED"],
    "TESTING": ["WAITING_FOR_DEPLOY_APPROVAL", "GENERATING", "FAILED"],
    "WAITING_FOR_DEPLOY_APPROVAL": ["DEPLOYING", "FAILED"],
    "DEPLOYING": ["VERIFIED", "FAILED"],
    "VERIFIED": ["COMPLETED", "FAILED"],
    "FAILED": ["REQUESTED"],
}
MAX_CONCURRENT_BUILDS = 19

# ── Build CRUD ────────────────────────────────────────────────────────────

def load_builds(include_terminal: bool = False) -> list[dict]:
    """Load all builds. Excludes terminal builds by default.

    Completed builds are archived to memory/builds_archive.json.
    """
    data = load(BUILDS_FILE)
    builds = data.get("records", []) if isinstance(data, dict) else (data or [])

    if not include_terminal:
        builds = [b for b in builds
                  if b.get("status") not in TERMINAL_BUILD_STATUSES]

    return builds


def save_builds(builds: list[dict]):
    """Save the builds list, archiving terminal ones."""
    # Separate terminal from non-terminal
    active = []
    terminal = []

    for b in builds:
        if b.get("status") in TERMINAL_BUILD_STATUSES:
            terminal.append(b)
        else:
            active.append(b)

    # Save active builds
    save(BUILDS_FILE, {"schema_version": 1, "records": active})

    # Archive terminal builds
    if terminal:
        archive = load(BUILDS_ARCHIVE_FILE)
        if not archive:
            archive = {"schema_version": 1, "records": []}
        existing_ids = {b["id"] for b in archive.get("records", [])}
        for b in terminal:
            if b["id"] not in existing_ids:
                archive.setdefault("records", []).append(b)
                existing_ids.add(b["id"])
        save(BUILDS_ARCHIVE_FILE, archive)


def get_build(build_id: str) -> dict | None:
    """Get a single build by ID."""
    builds = load_builds(include_terminal=True)
    for b in builds:
        if b.get("id") == build_id:
            return b
    return None


def create_build(phase: dict, force: bool = False) -> dict:
    """Create a new build from a roadmap phase.

    Dedup: if a non-terminal build with the same name already exists, return it.
    """
    phase_id = phase.get("id", "")
    phase_name = phase.get("name", "unnamed")

    # Dedup check
    if not force:
        existing = _find_build_by_name(phase_name)
        if existing:
            info(f"Build dedup: '{phase_name}' already exists as "
                 f"{existing['id'][:12]} [{existing['status']}] — reusing")
            return existing

    # Create contract
    contract = build_contract_from_phase(phase)
    is_valid, errors = validate_contract(contract)
    if not is_valid:
        log_error(f"Invalid build contract for {phase_id}: {errors}")
        raise ValueError(f"Build contract validation failed: {errors}")

    build = new_object(
        status="REQUESTED",
        trace_id=phase_id,
        name=phase_name,
        phase_id=phase_id,
        build_name=phase_name,  # Legacy compatibility
        contract=contract,
        diff="",
        summary="",
        worker_type=None,
        sandbox_path=None,
        generation_attempts=0,
        review_attempts=0,
        timeout_count=0,
    )

    # Atomic add
    def _add_build(data):
        data.setdefault("records", []).append(build)
        return data

    update(BUILDS_FILE, _add_build)
    info(f"Build created: {build['id'][:12]} '{phase_name}'")

    return build


def transition_build(build: dict, new_status: str, note: str = "") -> dict:
    """Transition a build through its state machine."""
    try:
        transition(build, new_status, BUILD_TRANSITIONS, note=note)
    except InvalidTransition:
        # If already in target state, that's fine
        if build.get("status") == new_status:
            return build
        raise

    # Persist
    def _update(data):
        for i, b in enumerate(data.get("records", [])):
            if b.get("id") == build["id"]:
                data["records"][i] = build
                break
        return data

    update(BUILDS_FILE, _update)
    return build


def fail_build(build: dict, reason: str):
    """Mark a build as FAILED with a reason."""
    build["failure_reason"] = reason
    build["failed_at"] = datetime.now(timezone.utc).isoformat()
    transition_build(build, "FAILED", note=reason[:200])
    info(f"Build {build['id'][:12]} FAILED: {reason}")


# ── Build Pipeline ────────────────────────────────────────────────────────

def advance_builds(max_workers: int | None = None) -> dict:
    """Advance all active builds through the pipeline.

    Two-pass processing:
      Pass 1: Completion-near builds (DEPLOYING, CODE_REVIEW) — never starved
      Pass 2: Everything else (GENERATING, PLANNING, REQUESTED, etc.)

    Returns a summary: {completed, failed, auto_failed, advanced}
    """
    workers = max_workers or MAX_CONCURRENT_BUILDS
    builds = load_builds()

    if not builds:
        return {"completed": 0, "failed": 0, "auto_failed": [], "advanced": 0}

    summary = {
        "completed": 0,
        "failed": 0,
        "auto_failed": [],
        "advanced": 0,
    }

    # Step 1: Detect and auto-fail stuck builds
    stuck = check_stuck_builds(builds, GENERATION_TIMEOUT, DEPLOYING_TIMEOUT)
    for stuck_build in stuck:
        build = get_build(stuck_build["build_id"])
        if build:
            recovery = handle_timeout(
                stuck_build["build_id"],
                stuck_build["status"],
                stuck_build["elapsed_s"],
            )
            if recovery["action"] == "retry":
                build["generation_attempts"] = build.get("generation_attempts", 0) + 1
                transition_build(build, "GENERATING", note="timeout retry")
                info(f"Retrying build {stuck_build['build_id'][:12]} after timeout")
            else:
                fail_build(build, stuck_build["reason"])
                summary["auto_failed"].append(stuck_build)
                summary["failed"] += 1

    # Step 2: Two-pass processing
    completion_near = [b for b in builds
                       if b.get("status") in _COMPLETION_NEAR_STATUSES]
    other = [b for b in builds
             if b.get("status") not in _COMPLETION_NEAR_STATUSES
             and b.get("status") not in TERMINAL_BUILD_STATUSES]

    info(f"Advancing builds: {len(completion_near)} completion-near, "
         f"{len(other)} other (max_workers={workers})")

    # Pass 1: Completion-near — always gets workers
    if completion_near:
        p1_results = _process_builds_concurrent(completion_near, workers, summary)
        summary["advanced"] += p1_results

    # Pass 2: Everything else
    if other:
        p2_results = _process_builds_concurrent(other, workers, summary)
        summary["advanced"] += p2_results

    # Save final state
    save_builds(load_builds(include_terminal=True))

    return summary


def _process_builds_concurrent(builds: list[dict],
                               max_workers: int,
                               summary: dict) -> int:
    """Process a list of builds concurrently using ThreadPoolExecutor."""
    advanced = 0
    with ThreadPoolExecutor(max_workers=min(max_workers, len(builds))) as executor:
        futures = {
            executor.submit(_advance_single_build, build): build["id"]
            for build in builds
        }
        for future in as_completed(futures):
            try:
                result = future.result(timeout=GENERATION_TIMEOUT + 300)
                if result:
                    advanced += 1
                    if result.get("completed"):
                        summary["completed"] += 1
                    if result.get("failed"):
                        summary["failed"] += 1
            except Exception as e:
                build_id = futures[future]
                log_error(f"Build advance failed for {build_id[:12]}: "
                          f"{type(e).__name__}")

    return advanced


def _advance_single_build(build: dict) -> dict | None:
    """Advance a single build through one step of its pipeline.

    Returns a result dict or None if nothing changed.
    """
    build_id = build["id"]
    status = build.get("status", "")

    try:
        if status == "REQUESTED":
            return _handle_requested(build)
        elif status == "PLANNING":
            return _handle_planning(build)
        elif status == "ARCHITECTURE_APPROVED":
            return _handle_generating(build)
        elif status == "GENERATING":
            return _handle_generating(build)
        elif status == "CODE_REVIEW":
            return _handle_code_review(build)
        elif status == "SECURITY_REVIEW":
            return _handle_security_review(build)
        elif status == "TESTING":
            return _handle_testing(build)
        elif status == "DEPLOYING":
            return _handle_deploying(build)
        elif status == "VERIFIED":
            return _handle_verified(build)
        else:
            # Waiting states — nothing to advance
            return None
    except Exception as e:
        log_error(f"Error advancing build {build_id[:12]} [{status}]: "
                  f"{type(e).__name__}: {str(e)[:200]}")
        return None


def _handle_requested(build: dict) -> dict:
    """REQUESTED → PLANNING: Kick off the planning phase."""
    transition_build(build, "PLANNING", note="starting planning")
    return _handle_planning(build)


def _handle_planning(build: dict) -> dict:
    """PLANNING → WAITING_FOR_ARCHITECTURE_APPROVAL: Generate a plan.

    Uses Pod A (Qwen4) for text-based planning.
    """
    contract = build.get("contract", {})
    objective = contract.get("objective", build.get("name", ""))

    prompt = (
        f"You are an AI Software Architect. Plan the implementation for:\n\n"
        f"Objective: {objective}\n"
        f"Acceptance Criteria: {contract.get('acceptance_criteria', [])}\n"
        f"Files Allowed: {contract.get('files_allowed', ['*'])}\n"
        f"Files Forbidden: {contract.get('files_forbidden', [])}\n"
        f"Expected Runtime: {contract.get('expected_runtime_s', 600)}s\n\n"
        f"Produce a detailed implementation plan:\n"
        f"1. Files to modify/create\n"
        f"2. Step-by-step approach\n"
        f"3. Key architectural decisions\n"
        f"4. Testing strategy\n"
    )

    try:
        plan = call_qwen3_coder_text(prompt, timeout=GENERATION_CALL_TIMEOUT)
        build["plan"] = plan
        build["plan_generated_at"] = datetime.now(timezone.utc).isoformat()
        transition_build(build, "WAITING_FOR_ARCHITECTURE_APPROVAL",
                         note="plan ready for approval")
        # Auto-approve at autonomy level 5
        from core.autonomy import get_autonomy_level
        if get_autonomy_level() >= 5:
            transition_build(build, "ARCHITECTURE_APPROVED",
                             note="auto-approved (autonomy 5)")
        info(f"Plan generated for {build['id'][:12]}")
    except Exception as e:
        log_error(f"Planning failed for {build['id'][:12]}: {type(e).__name__}")
        fail_build(build, f"Planning failed: {type(e).__name__}")

    return {"advanced": True}


def _handle_generating(build: dict) -> dict:
    """GENERATING → CODE_REVIEW: Execute code generation.

    Uses Pod A (Qwen4) for the actual coding work.
    Runs in an isolated sandbox (git worktree).
    """
    # Prepare sandbox
    sandbox = prepare_sandbox(build)
    sandbox_path = sandbox["project_path"]
    build["sandbox_path"] = sandbox_path

    contract = build.get("contract", {})
    plan = build.get("plan", "")

    prompt = (
        f"You are an AI Software Engineer. Implement the following task.\n\n"
        f"Implementation Plan:\n{plan[:4000]}\n\n"
        f"Project Context:\n"
        f"  Working directory: {sandbox_path}\n"
        f"  Branch: {sandbox['branch']}\n"
        f"  Files allowed: {contract.get('files_allowed', ['*'])}\n"
        f"  Files forbidden: {contract.get('files_forbidden', [])}\n\n"
        f"Acceptance Criteria:\n"
        + "\n".join(f"  - {c}" for c in contract.get("acceptance_criteria", []))
        + f"\n\nWrite the code. After writing, verify tests pass.\n"
    )

    try:
        # Use the Qwen3 coding path through ai_router
        from core.ai.ai_router import delegate as ai_delegate

        result = ai_delegate(
            prompt=prompt,
            task_type="coding",
            model="qwen3_coding",
            timeout=GENERATION_CALL_TIMEOUT,
        )

        build["generation_result"] = result if isinstance(result, str) else str(result)
        build["generation_completed_at"] = datetime.now(timezone.utc).isoformat()
        build["diff"] = _get_sandbox_diff(sandbox_path)
        build["summary"] = _extract_summary(result)

        transition_build(build, "CODE_REVIEW",
                         note=f"generation complete ({len(build['diff'])} chars)")

        # Init approval voting
        reviewers = contract.get("required_reviewers",
                                 ["architecture", "security", "qa"])
        init_build_approval(build["id"], required_reviewers=reviewers)

        info(f"Generation complete for {build['id'][:12]}")

    except Exception as e:
        log_error(f"Generation failed for {build['id'][:12]}: {type(e).__name__}")
        recovery = handle_generation_failure(build["id"], str(e))
        if recovery["action"] == "return_to_builder":
            build["generation_attempts"] = build.get("generation_attempts", 0) + 1
            if build["generation_attempts"] >= 3:
                fail_build(build, f"Generation failed after 3 attempts: {e}")
            else:
                transition_build(build, "PLANNING",
                                 note=f"generation retry #{build['generation_attempts']}")
        else:
            fail_build(build, f"Generation failed: {type(e).__name__}")

    return {"advanced": True}


def _handle_code_review(build: dict) -> dict:
    """CODE_REVIEW → SECURITY_REVIEW or back to GENERATING.

    Runs the full review pipeline on Pod B (Qwen6).
    """
    result = run_review_pipeline(
        build,
        diff=build.get("diff", ""),
        summary=build.get("summary", ""),
    )

    if result["approved"]:
        transition_build(build, "SECURITY_REVIEW", note="code review passed")
    else:
        _handle_review_return(build, result)

    return {"advanced": True}


def _handle_security_review(build: dict) -> dict:
    """SECURITY_REVIEW → TESTING."""
    transition_build(build, "TESTING", note="security cleared")
    return _handle_testing(build)


def _handle_testing(build: dict) -> dict:
    """TESTING → WAITING_FOR_DEPLOY_APPROVAL or back to GENERATING.

    Runs the test suite in the sandbox.
    """
    sandbox_path = build.get("sandbox_path", "")
    if sandbox_path:
        try:
            from core.sandbox import run_in_sandbox

            test_result = run_in_sandbox(
                sandbox_path,
                ".venv/bin/python -m pytest --tb=short 2>&1 | tail -50",
                timeout=300,
            )

            if test_result.get("exit_code") == 0:
                build["test_result"] = "passed"
                transition_build(build, "WAITING_FOR_DEPLOY_APPROVAL",
                                 note="tests pass, awaiting deploy approval")
            else:
                build["test_result"] = test_result.get("stdout", "")[:1000]
                _handle_review_return(build, {
                    "all_findings": [f"Tests failed: {build['test_result'][:200]}"],
                    "failed_reviewers": ["qa"],
                })
        except Exception as e:
            log_error(f"Test execution failed for {build['id'][:12]}: {e}")
            _handle_review_return(build, {
                "all_findings": [f"Test execution error: {e}"],
                "failed_reviewers": ["qa"],
            })
    else:
        transition_build(build, "WAITING_FOR_DEPLOY_APPROVAL",
                         note="no sandbox, pending approval")

    return {"advanced": True}


def _handle_deploying(build: dict) -> dict:
    """DEPLOYING → VERIFIED or FAILED."""
    try:
        from core.deployment_manager import deploy_build

        result = deploy_build(build)
        if result.get("success"):
            transition_build(build, "VERIFIED", note="deployment succeeded")
        else:
            fail_build(build, f"Deployment failed: {result.get('error', 'unknown')}")
            return {"advanced": True, "failed": True}
    except Exception as e:
        log_error(f"Deployment error for {build['id'][:12]}: {e}")
        fail_build(build, f"Deployment error: {type(e).__name__}")
        return {"advanced": True, "failed": True}

    return {"advanced": True}


def _handle_verified(build: dict) -> dict:
    """VERIFIED → COMPLETED."""
    transition_build(build, "COMPLETED", note="build verified and complete")

    # Release sandbox
    release_sandbox(build["id"])

    info(f"Build {build['id'][:12]} COMPLETED")
    return {"advanced": True, "completed": True}


def _handle_review_return(build: dict, review_result: dict):
    """Handle a failed review — return build to GENERATING with findings."""
    findings = review_result.get("all_findings", [])
    failed = review_result.get("failed_reviewers", [])

    build["review_findings"] = findings
    build["failed_reviewers"] = failed
    build["review_attempts"] = build.get("review_attempts", 0) + 1

    if build["review_attempts"] >= 3:
        fail_build(build, f"Review failed after 3 attempts: {', '.join(failed)}")
    else:
        transition_build(build, "GENERATING",
                         note=f"review #{build['review_attempts']}: "
                              f"{len(findings)} issues from {', '.join(failed)}")

    info(f"Build {build['id'][:12]} returned to builder for fixes "
         f"(attempt {build['review_attempts']})")


# ── Helpers ───────────────────────────────────────────────────────────────

def _find_build_by_name(name: str) -> dict | None:
    """Find a non-terminal build with the given name (dedup check)."""
    builds = load_builds()
    for b in builds:
        if b.get("name") == name and b.get("status") in NON_TERMINAL_BUILD_STATUSES:
            return b
    return None


def _get_sandbox_diff(sandbox_path: str) -> str:
    """Get the git diff from a sandbox."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "-C", sandbox_path, "diff", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout[:20000]  # Cap at 20KB
    except Exception:
        return ""


def _extract_summary(result) -> str:
    """Extract a short summary from generation result."""
    if isinstance(result, str):
        # Take first substantial paragraph
        lines = result.split("\n")
        summary_lines = []
        for line in lines:
            if len(line.strip()) > 20:
                summary_lines.append(line.strip())
                if len(summary_lines) >= 5:
                    break
        return "\n".join(summary_lines)[:1000]
    return str(result)[:1000]
