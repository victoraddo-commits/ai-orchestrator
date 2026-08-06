"""Kai V3 Review Pipeline — Multi-agent review with configurable voting.

Five specialized reviewers, all running on Pod B (Qwen6):
  ArchitectureReviewer, SecurityReviewer, PerformanceReviewer,
  QAReviewer, DocumentationReviewer

Pipeline flow:
  Builder completes → Architecture → Security → Performance → QA → Docs → Approval → Deploy

Each reviewer is an independent Pod B LLM call against the build's diff + summary.
Failed review returns findings to builder for fixes.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from core.llm_clients import call_qwen6
from core.logger import info, error as log_error

from core.v3.approval_system import (
    cast_vote, init_build_approval,
    get_approval_status, is_build_approved,
)

DEFAULT_REVIEWERS = [
    "architecture",
    "security",
    "performance",
    "qa",
    "documentation",
]

REVIEWER_PROMPTS = {
    "architecture": """You are an Architecture Reviewer. Review this build code for design correctness, maintainability, and scalability.

Does it follow existing patterns? Is it maintainable? Will it scale?

Build Summary:
{summary}

Code Changes (diff):
{diff}

Respond with:
1. APPROVED or REJECTED (one word on first line)
2. Confidence: 0.0-1.0
3. If REJECTED, list each finding on a new line as "- finding description"
4. If APPROVED, list any suggestions on a new line as "- suggestion"

Your review:""",

    "security": """You are a Security Reviewer. Review this build code for vulnerabilities, exposed secrets, missing authorization, injection vectors, and unsafe permissions.

Build Summary:
{summary}

Code Changes (diff):
{diff}

Respond with:
1. APPROVED or REJECTED (one word on first line)
2. Confidence: 0.0-1.0
3. If REJECTED, list each finding on a new line as "- finding description"
4. If APPROVED, list any suggestions on a new line as "- suggestion"

Your review:""",

    "performance": """You are a Performance Reviewer. Review this build code for performance issues: N+1 queries, unbounded loops, missing indexes, excessive allocations, memory leaks, slow patterns.

Build Summary:
{summary}

Code Changes (diff):
{diff}

Respond with:
1. APPROVED or REJECTED (one word on first line)
2. Confidence: 0.0-1.0
3. If REJECTED, list each finding on a new line as "- finding description"
4. If APPROVED, list any suggestions on a new line as "- suggestion"

Your review:""",

    "qa": """You are a QA Reviewer. Review this build to verify tests cover the acceptance criteria and there are no regressions against existing functionality.

Build Summary:
{summary}

Code Changes (diff):
{diff}

Respond with:
1. APPROVED or REJECTED (one word on first line)
2. Confidence: 0.0-1.0
3. If REJECTED, list each finding on a new line as "- finding description"
4. If APPROVED, list any suggestions on a new line as "- suggestion"

Your review:""",

    "documentation": """You are a Documentation Reviewer. Review this build to verify new code is documented: docstrings, README updates, inline comments for complex logic, and any relevant API docs.

Build Summary:
{summary}

Code Changes (diff):
{diff}

Respond with:
1. APPROVED or REJECTED (one word on first line)
2. Confidence: 0.0-1.0
3. If REJECTED, list each finding on a new line as "- finding description"
4. If APPROVED, list any suggestions on a new line as "- suggestion"

Your review:""",
}


def run_review_pipeline(build: dict,
                        diff: str = "",
                        summary: str = "",
                        required_reviewers: list[str] | None = None,
                        concurrent: bool = True) -> dict:
    """Run the full review pipeline for a build.

    Args:
        build: The build dict
        diff: Git diff of the build's changes
        summary: Summary of what the build implemented
        required_reviewers: List of reviewer types (default: all 5)
        concurrent: If True, run all reviewers in parallel (Pod B concurrency)

    Returns:
        {
            "approved": bool,
            "votes": {reviewer: {approved, findings, confidence}},
            "failed_reviewers": [...],
            "all_findings": [...],
        }
    """
    build_id = build["id"]
    reviewers = required_reviewers or DEFAULT_REVIEWERS

    init_build_approval(build_id, required_reviewers=reviewers)
    info(f"Review pipeline started for {build_id[:12]} "
         f"({len(reviewers)} reviewers)")

    if concurrent:
        results = _run_reviewers_concurrent(build, diff, summary, reviewers)
    else:
        results = _run_reviewers_sequential(build, diff, summary, reviewers)

    # Cast votes
    all_findings = []
    failed_reviewers = []

    for reviewer_result in results:
        reviewer = reviewer_result["reviewer"]
        cast_vote(
            build_id, reviewer,
            approved=reviewer_result["approved"],
            findings=reviewer_result["findings"],
            confidence=reviewer_result["confidence"],
        )
        if not reviewer_result["approved"]:
            failed_reviewers.append(reviewer)
            all_findings.extend(reviewer_result["findings"])

    overall_approved = is_build_approved(build_id)

    info(f"Review pipeline complete for {build_id[:12]}: "
         f"approved={overall_approved}, "
         f"{len(failed_reviewers)}/{len(reviewers)} reviewers rejected")

    return {
        "approved": overall_approved,
        "votes": {r["reviewer"]: {
            "approved": r["approved"],
            "findings": r["findings"],
            "confidence": r["confidence"],
        } for r in results},
        "failed_reviewers": failed_reviewers,
        "all_findings": all_findings,
    }


def run_single_review(build: dict, reviewer: str,
                      diff: str = "",
                      summary: str = "",
                      timeout: int = 180) -> dict:
    """Run a single reviewer against a build. Used for incremental re-reviews.

    This only re-runs reviewers that failed previously — not all 5.
    """
    prompt = _build_review_prompt(reviewer, summary, diff)
    result = _call_reviewer(reviewer, prompt, timeout)

    findings = result.get("findings", [])
    confidence = result.get("confidence", 0.5)

    cast_vote(
        build["id"], reviewer,
        approved=result["approved"],
        findings=findings,
        confidence=confidence,
    )

    return {
        "reviewer": reviewer,
        "approved": result["approved"],
        "findings": findings,
        "confidence": confidence,
    }


def _run_reviewers_concurrent(build: dict, diff: str, summary: str,
                              reviewers: list[str]) -> list[dict]:
    """Run all reviewers in parallel on Pod B."""
    results = []

    with ThreadPoolExecutor(max_workers=len(reviewers)) as executor:
        futures = {}
        for reviewer in reviewers:
            prompt = _build_review_prompt(reviewer, summary, diff)
            future = executor.submit(_call_reviewer, reviewer, prompt)
            futures[future] = reviewer

        for future in as_completed(futures):
            reviewer = futures[future]
            try:
                result = future.result(timeout=300)
                results.append({"reviewer": reviewer, **result})
            except Exception as e:
                log_error(f"Reviewer {reviewer} failed: {type(e).__name__}")
                results.append({
                    "reviewer": reviewer,
                    "approved": False,
                    "findings": [f"Reviewer {reviewer} failed: {type(e).__name__}"],
                    "confidence": 0.0,
                })

    return results


def _run_reviewers_sequential(build: dict, diff: str, summary: str,
                               reviewers: list[str]) -> list[dict]:
    """Run reviewers one at a time (sequential fallback)."""
    results = []
    for reviewer in reviewers:
        prompt = _build_review_prompt(reviewer, summary, diff)
        result = _call_reviewer(reviewer, prompt)
        results.append({"reviewer": reviewer, **result})
    return results


def _build_review_prompt(reviewer: str, summary: str, diff: str) -> str:
    """Build the review prompt for a specific reviewer type."""
    template = REVIEWER_PROMPTS.get(
        reviewer,
        f"You are a {reviewer} reviewer. Review this build code.\n"
        f"Summary:\n{summary}\n\nChanges:\n{diff}\n\n"
        f"Respond with APPROVED or REJECTED, confidence, and findings.",
    )
    return template.format(summary=summary or "No summary provided",
                           diff=diff[:8000] or "No diff available")


def _call_reviewer(reviewer: str, prompt: str,
                   timeout: int = 180) -> dict:
    """Call Pod B (Qwen6) with a review prompt and parse the response."""
    try:
        response = call_qwen6(prompt, timeout=timeout)

        approved, confidence, findings = _parse_review_response(response)

        return {
            "approved": approved,
            "confidence": confidence,
            "findings": findings,
        }

    except Exception as e:
        log_error(f"Review call failed for {reviewer}: {type(e).__name__}")
        return {
            "approved": False,
            "confidence": 0.0,
            "findings": [f"Reviewer {reviewer} failed: {type(e).__name__}"],
        }


def _parse_review_response(response: str) -> tuple[bool, float, list[str]]:
    """Parse a reviewer's text response into structured data.

    Expected format:
    APPROVED or REJECTED (first word)
    Confidence: 0.8
    - finding 1
    - finding 2
    """
    lines = response.strip().split("\n")

    # Parse approval
    first_line = lines[0].strip().upper() if lines else ""
    approved = "APPROVED" in first_line and "REJECTED" not in first_line

    # Parse confidence
    confidence = 0.5
    for line in lines[1:]:
        line_lower = line.strip().lower()
        if "confidence" in line_lower or "conf" in line_lower:
            # Extract number
            import re
            match = re.search(r"(\d+\.?\d*)", line)
            if match:
                try:
                    confidence = float(match.group(1))
                    confidence = max(0.0, min(1.0, confidence))
                except ValueError:
                    pass
            break

    # Parse findings
    findings = []
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            findings.append(stripped[2:].strip())

    # If no structured findings but REJECTED, use the whole response
    if not findings and not approved:
        # Take everything after the first line as one finding
        rest = "\n".join(lines[1:]).strip()
        if rest:
            findings = [rest[:500]]

    return approved, confidence, findings
