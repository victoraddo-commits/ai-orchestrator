import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.memory import load, save, update
from core.lifecycle import new_object, transition, InvalidTransition
from core.ai.ai_router import delegate, AllProvidersFailed
import core.ai_provider as ai_provider
from core.repo_manager import create_local_repo
from core.project_templates import get_template
from core.security_scanner import run_all_scans
from core.deployment_manager import deploy_build
from core.plugin_deployer import redeploy_plugin_if_needed
from core.service_restarter import restart_services_if_needed
from core.remediation import attempt_rollback
from core.build_learning import record_build_outcome, TERMINAL_STATUSES
from core.approval import create_build_approval

# Planning tasks are text-in/text-out and don't need Claude's file/tool
# access -- only actual code generation does. Routing them through the
# multi-provider AI router (Gemini/OpenRouter/Minimax first, Claude only as
# the guaranteed-capable last resort) means the step that runs for every
# roadmap phase attempt, and that caused every one of tonight's Claude-side
# incidents, no longer needs to touch Claude at all in the common case.
PLANNING_TIMEOUT = 180

# Generation involves real file writes/tool calls/tests, not a quick text
# response -- confirmed live: 13C's generation hit the 300s wall-clock
# ceiling (core.coding_bridge's default) while still actively working on a
# genuinely larger module, so it was raised to 600s. Confirmed live again
# 2026-07-29: 13Q's opencode_claude and opencode attempts both ran out the
# clock at 600s while still actively touching files (5+ files, repeated
# format/test iterations) -- a genuinely large architecture change, not a
# stuck/broken provider. Raised again; safe under the systemd unit's
# WatchdogSec=1800 (see /etc/systemd/system/ai-orchestrator.service.d/
# override.conf, outside this repo) with headroom to spare.
GENERATION_TIMEOUT = 1200


# The code-review step is a plain text-in/text-out Claude call over the
# build's generation summary -- no file writes or tool use -- so planning's
# text-task budget is the right scale, not generation's 600s ceiling.
CODE_REVIEW_TIMEOUT = 180

PROVIDERS_CONFIG_PATH = Path("config") / "providers.yaml"

DEFAULT_MAX_CONCURRENT_BUILDS = 2


def _load_max_concurrent_builds():
    # config/providers.yaml: max_concurrent_builds. Any missing/unreadable/
    # nonsensical value falls back to the default -- a config problem must
    # never stop builds from advancing at all.
    try:
        import yaml

        config = yaml.safe_load(PROVIDERS_CONFIG_PATH.read_text()) or {}
        value = int(config.get("max_concurrent_builds", DEFAULT_MAX_CONCURRENT_BUILDS))
        return value if value >= 1 else DEFAULT_MAX_CONCURRENT_BUILDS
    except Exception:
        return DEFAULT_MAX_CONCURRENT_BUILDS


MAX_CONCURRENT_BUILDS = _load_max_concurrent_builds()


# Deliberately a separate store from approval_queue.json -- that queue is
# swept every cycle by remediation_runner.process(), which calls
# execute_action() (docker-restart only) on every approved request
# regardless of action name. Build approvals need their own isolated queue
# so they never collide with that pipeline.
BUILDS_FILE = "builds.json"

# TESTING/DEPLOYING/VERIFIED are defined here for forward compatibility with
# Phases 12D-12G, which will add the logic that actually drives builds
# through them. Until then, advance_builds() only ever takes GENERATING to
# COMPLETED or FAILED directly.
#
# WAITING_FOR_USER_INPUT vs. WAITING_FOR_ARCHITECTURE_APPROVAL /
# WAITING_FOR_DEPLOY_APPROVAL is a deliberate split (Kai Approval Center
# integration): free-text clarification (submit_answer, "build answers") only
# ever happens from WAITING_FOR_USER_INPUT, which loops back to PLANNING.
# A plan/security-review result that's actually ready for a decision instead
# goes to one of the WAITING_FOR_*_APPROVAL states, each of which creates a
# formal Approval object (core.approval.create_build_approval) so the
# decision surfaces in the CloudCLI Approval Center rather than as a build
# question -- see _run_planning/_run_generation.
BUILD_TRANSITIONS = {
    "REQUESTED": ["PLANNING", "FAILED"],
    "PLANNING": ["WAITING_FOR_USER_INPUT", "WAITING_FOR_ARCHITECTURE_APPROVAL", "FAILED"],
    "WAITING_FOR_USER_INPUT": ["PLANNING", "FAILED"],
    "WAITING_FOR_ARCHITECTURE_APPROVAL": ["ARCHITECTURE_APPROVED", "FAILED"],
    "ARCHITECTURE_APPROVED": ["GENERATING", "FAILED"],
    # 13R: successful generation now goes through CODE_REVIEW (an advisory
    # Claude review, see _run_code_review) before SECURITY_REVIEW.
    # SECURITY_REVIEW/COMPLETED are kept as pre-existing theoretical direct
    # targets from GENERATING (unused by current code either way) -- see
    # Phase 12E's scoping note on TESTING.
    "GENERATING": ["CODE_REVIEW", "TESTING", "SECURITY_REVIEW", "COMPLETED", "FAILED"],
    "CODE_REVIEW": ["SECURITY_REVIEW", "FAILED"],
    "TESTING": ["SECURITY_REVIEW", "FAILED"],
    "SECURITY_REVIEW": ["WAITING_FOR_DEPLOY_APPROVAL", "FAILED"],
    "WAITING_FOR_DEPLOY_APPROVAL": ["DEPLOYING", "FAILED"],
    "DEPLOYING": ["VERIFIED", "FAILED"],
    "VERIFIED": ["COMPLETED", "ROLLED_BACK"],
    # A deployed-and-completed build can still be rolled back later (e.g. a
    # bug surfaces after the fact) -- COMPLETED isn't fully terminal for
    # builds that reached production.
    "COMPLETED": ["ROLLED_BACK"],
    "FAILED": [],
    "ROLLED_BACK": [],
}


def load_builds():
    builds = load(BUILDS_FILE)

    if not isinstance(builds, list):
        builds = []

    return builds


def save_builds(builds):
    save(BUILDS_FILE, builds)


def create_build(name, description, project_path, template=None):
    if template is not None and get_template(template) is None:
        raise ValueError(f"Unknown project template: {template!r}")

    builds = load_builds()

    build = new_object(
        "REQUESTED",
        name=name,
        description=description,
        project_path=project_path,
        template=template,
        qa_history=[],
        plan=None,
        planned_by=None,
        pending_question=None,
        generation_result=None,
        generated_by=None,
        security_report=None,
        deployment=None,
    )

    builds.append(build)
    save_builds(builds)

    return build


def list_builds():
    return load_builds()


def get_build(build_id):
    for build in load_builds():
        if build.get("id") == build_id:
            return build

    return None


def _update(build_id, mutate):
    builds = load_builds()

    for build in builds:
        if build.get("id") == build_id:
            mutate(build)
            save_builds(builds)
            return build

    return None


def _require_status(build, expected):
    # transition()'s table alone isn't enough of a guard here: several of
    # these destinations (e.g. PLANNING) are legitimately reachable from more
    # than one source state depending on which caller is driving the build,
    # so each entry point must assert its own expected starting state too.
    if build["status"] != expected:
        raise InvalidTransition(
            f"cannot perform this action while build is {build['status']!r} (expected {expected!r})"
        )


def submit_answer(build_id, answer):
    build = get_build(build_id)
    if build is None:
        return None

    _require_status(build, "WAITING_FOR_USER_INPUT")

    def mutate(b):
        transition(b, "PLANNING", BUILD_TRANSITIONS)
        b.setdefault("qa_history", []).append({"answer": answer})
        b["pending_question"] = None

    return _update(build_id, mutate)


def approve_architecture(build_id, operator=None, note=None):
    build = get_build(build_id)
    if build is None:
        return None

    _require_status(build, "WAITING_FOR_ARCHITECTURE_APPROVAL")

    def mutate(b):
        transition(b, "ARCHITECTURE_APPROVED", BUILD_TRANSITIONS, note=note)
        b["architecture_approved_by"] = operator

    return _update(build_id, mutate)


def reject_architecture(build_id, operator=None, note=None):
    build = get_build(build_id)
    if build is None:
        return None

    _require_status(build, "WAITING_FOR_ARCHITECTURE_APPROVAL")

    def mutate(b):
        transition(b, "FAILED", BUILD_TRANSITIONS, note=note)
        b["failure_reason"] = note or "Architecture approval rejected"
        b["architecture_rejected_by"] = operator
        _record_if_terminal(b)

    return _update(build_id, mutate)


def start_generation(build_id):
    build = get_build(build_id)
    if build is None:
        return None

    _require_status(build, "ARCHITECTURE_APPROVED")

    def mutate(b):
        transition(b, "GENERATING", BUILD_TRANSITIONS)

    return _update(build_id, mutate)


def approve_deploy(build_id, operator=None, note=None):
    build = get_build(build_id)
    if build is None:
        return None

    _require_status(build, "WAITING_FOR_DEPLOY_APPROVAL")

    def mutate(b):
        transition(b, "DEPLOYING", BUILD_TRANSITIONS, note=note)
        b["deploy_approved_by"] = operator

    return _update(build_id, mutate)


def reject_deploy(build_id, operator=None, note=None):
    build = get_build(build_id)
    if build is None:
        return None

    _require_status(build, "WAITING_FOR_DEPLOY_APPROVAL")

    def mutate(b):
        transition(b, "FAILED", BUILD_TRANSITIONS, note=note)
        b["failure_reason"] = note or "Deploy approval rejected"
        b["deploy_rejected_by"] = operator
        _record_if_terminal(b)

    return _update(build_id, mutate)


def rollback_deployment(build_id):
    build = get_build(build_id)
    if build is None:
        return None

    deployment = build.get("deployment")
    if not deployment or not deployment.get("remediation_id"):
        raise ValueError(f"Build {build_id!r} has no deployment to roll back")

    # Capture the remediation's rollback outcome on the build so
    # core.build_learning._derive_rollback_root_cause() has something concrete
    # to read when record_build_outcome runs -- previously a build transitioned
    # to ROLLED_BACK without any record of *why*.
    rollback_result = attempt_rollback(deployment["remediation_id"])
    rollback_info = (rollback_result or {}).get("rollback") or {}

    def mutate(b):
        b_deployment = b.setdefault("deployment", {})
        b_deployment["rollback"] = rollback_info
        transition(b, "ROLLED_BACK", BUILD_TRANSITIONS, note="manual rollback requested")
        _record_if_terminal(b)

    return _update(build_id, mutate)


def _template_context(build):
    template = get_template(build.get("template"))
    return f"\nTemplate to use as a starting point: {template['base_instruction']}\n" if template else ""


def _planning_prompt(build):
    qa_context = "\n".join(
        f"- Q/A: {entry['answer']}" for entry in build.get("qa_history", [])
    )

    return (
        "You are in the planning phase of a new application build. "
        "Do NOT write, edit, or modify any files, and do NOT run commands that "
        "change anything -- only read the existing project if useful and respond "
        "with text.\n\n"
        f"Requested application: {build['name']}\n"
        f"Description: {build['description']}\n"
        + _template_context(build)
        + (f"\nPrior clarifications:\n{qa_context}\n" if qa_context else "")
        + "\nPropose an architecture/implementation plan. If anything is "
        "ambiguous or you need a decision from the requester, ask for it "
        "explicitly in your response."
    )


# Prepended to every generation instruction regardless of which provider
# (claude / opencode / opencode_claude) ends up handling it -- every coding
# agent Kai dispatches is held to the same "write tests, run them, verify
# before claiming done" standard this project's own workflow follows.
GENERATION_DISCIPLINE_PREAMBLE = (
    "Follow disciplined engineering practice: write tests for new "
    "behavior before or alongside the implementation, run them, and "
    "verify they actually pass before considering any part of this "
    "done. Do not claim something works without having run it.\n\n"
)


def _generation_prompt(build):
    return (
        GENERATION_DISCIPLINE_PREAMBLE
        + "The following architecture plan was reviewed and approved by the "
        "requester. Implement it now: write the code, and commit your work "
        "with git as you go.\n\n"
        f"Application: {build['name']}\n"
        f"Description: {build['description']}\n"
        + _template_context(build)
        + f"Approved plan:\n{build.get('plan') or ''}"
    )


def _ensure_repo(build):
    # A dual-repo self-build workspace (roadmap_manager._create_isolated_
    # self_clone(include_plugin=True)) is a plain parent directory holding
    # two sibling clones -- the build branch must exist in each actual repo,
    # and the parent itself must never be git-inited (create_local_repo
    # would happily do so, burying both clones as untracked nested repos).
    # For every other build, self_build_repo_paths() is just [project_path],
    # i.e. exactly the old single-repo behavior. Imported lazily:
    # core.roadmap_manager imports this module at import time.
    from core.roadmap_manager import self_build_repo_paths

    for repo_path in self_build_repo_paths(build["project_path"]):
        create_local_repo(repo_path, branch=f"build-{build['id']}")


def _record_if_terminal(build):
    if build.get("status") in TERMINAL_STATUSES:
        record_build_outcome(build)


# Confirmed live 2026-07-29 (13P, then again on 13Y's own plan -- a plan
# that discusses this exact detection logic inevitably quotes '?' as
# example text): a bare "'?' in plan_text" false-positives on any rhetorical
# closing solicitation ("any objections?") and on any '?' appearing as
# illustrative/quoted text anywhere in the document, not just a genuine
# request for human input. A real open question is the plan's actual closing
# ask, not an example quoted earlier in the document -- so only the tail
# (the last heading-demarcated section, if the plan has one, else the last
# paragraph) is inspected, and within that tail a sentence matching a known
# rhetorical sign-off phrase is excluded. Deliberately no "does it offer a
# concrete alternative" check -- an earlier version tried gating on the
# presence of the word "or", but that matches filler ("objections or final
# check") just as readily as a real choice ("database A or database B"),
# which is worse than not checking at all.
_SIGNOFF_PATTERNS = re.compile(
    r"any objections|any concerns|any (?:other |additional |specific )?edge cases"
    r"|shall we proceed|does this look good|let me know if"
    r"|ready to proceed|ready for implementation|before proceeding|before we proceed"
    r"|before coding begins|before implementation"
    r"|that we need to account for|need to account for"
    r"|you would like addressed|you would like considered",
    re.IGNORECASE,
)

# Tolerates the heading variants actually observed live: a bare "Questions
# Needed?", a numbered "#### 4. Questions / Clarifications for the
# Requester", and 13P's own "Decision Points (if applicable)".
_CLARIFICATION_HEADING = re.compile(
    r"^#{0,6}.{0,24}?\b(?:questions?|clarifications?|decisions?\s+needed|decision\s+points?)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


def _tail_for_clarification_check(plan_text):
    heading_match = None
    for match in _CLARIFICATION_HEADING.finditer(plan_text):
        heading_match = match

    if heading_match is not None:
        return plan_text[heading_match.end():]

    paragraphs = [p for p in re.split(r"\n\s*\n", plan_text) if p.strip()]
    return paragraphs[-1] if paragraphs else plan_text


# Real HTML/SVG element names a legitimate frontend-touching plan might
# reasonably mention inline (e.g. "add a <button> that POSTs the message").
# Everything else bare-tag-shaped is presumed to be hallucinated tool/
# invocation syntax rather than markup -- confirmed live 2026-07-30 across
# three DIFFERENT tag names in one session (<bash>...</bash> on 13V,
# <read_file><path>...</path></read_file> on 13M) plus a non-tag variant
# (```bash fenced block on 17B's retry) -- enumerating specific tool-tag
# names one at a time loses this race every time a provider/framework uses
# a new one. A general "any non-HTML bare tag is suspicious" rule doesn't.
_HTML_TAG_ALLOWLIST = frozenset({
    "div", "span", "button", "input", "form", "label", "p", "a", "ul", "li",
    "ol", "table", "tr", "td", "th", "thead", "tbody",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "header", "footer", "nav", "img", "textarea", "select",
    "option", "style", "script", "svg", "path", "br", "hr", "code", "pre",
    "strong", "em", "b", "i", "small", "iframe", "canvas", "video", "audio",
    "source", "link", "meta", "title", "html", "head", "body",
})

_XML_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9_-]*)((?:\s*:\s*[a-zA-Z][a-zA-Z0-9_-]*)?)\s*/?\s*>")

# Bare JSON tool shape ({"tool_calls": [...]}) that may leak into a text
# response from the planning provider, and fenced shell code blocks
# (```bash ... ```) -- confirmed live 2026-07-30 (17B retry,
# planned_by=deepseek): a text_task call with no real tool access produced
# "I'll explore the repositories ... ```bash\npwd && ls -la\n```" as its
# entire "plan". Restricted to shell-family language tags specifically, not
# every fenced code block -- a legitimate plan may include an illustrative
# python/json snippet describing a design; it would never fence a shell
# command it intends to have executed.
_OTHER_LEAK_PATTERNS = re.compile(
    r'"[a-zA-Z0-9_-]*tool_calls?"\s*:\s*|'
    r"```\s*(?:bash|sh|shell|zsh|console)\b",
    re.IGNORECASE,
)


def _has_hallucinated_tag(text):
    for match in _XML_TAG.finditer(text):
        namespace = match.group(3)
        if namespace:
            return True  # <provider:tool_call> -- never legitimate prose
        if match.group(2).lower() not in _HTML_TAG_ALLOWLIST:
            return True
    return False

# Anything shorter than this (after stripping whitespace) is considered
# near-empty and not a usable plan.
_MIN_PLAN_LENGTH = 10

# Three consecutive unusable responses from any provider(s) is a systemic
# failure -- the ai_router rotation has had three shots at this plan and
# every one returned garbage.  Failing the build at that point is a sane
# upper bound rather than letting it bounce forever.
_MAX_CONSECUTIVE_REJECTIONS = 3


def _looks_like_tool_call_leak(text):
    if not text or len(text.strip()) < _MIN_PLAN_LENGTH:
        return True

    if _OTHER_LEAK_PATTERNS.search(text):
        return True

    if _has_hallucinated_tag(text):
        return True

    return False


def _looks_like_no_op_generation(result):
    # opencode_bridge.run_coding_task (and its equivalents) define success
    # purely as "process exited cleanly with no tool errors" -- a coding
    # agent that stops early (hits its own internal turn/step budget,
    # decides it's "done exploring" without ever implementing anything) can
    # exit 0 with that flag still set. Confirmed live 2026-07-29: build
    # 1b3875d7 (13U) reported success via opencode_claude_sonnet with
    # files_changed=[] and no commits -- the response_text was mid-
    # exploration ("Now let's check how this workspace relates to...").
    # Every generation prompt explicitly instructs "write the code, and
    # commit your work with git as you go" (GENERATION_DISCIPLINE_PREAMBLE
    # + _generation_prompt), so a claimed success with neither files_changed
    # nor commits never represents real completed work for a build task --
    # treat it the same as an outright failure rather than letting it
    # cascade through code review/security review to a human-facing deploy
    # approval for a no-op diff.
    return not (result.get("files_changed") or result.get("commits"))


def _plan_needs_clarification(plan_text):
    # A plan that's still asking an open question isn't a concrete proposal
    # yet -- surfacing it as a formal Approval would ask a human to
    # approve/reject something that isn't actually decided. Only a plan
    # without an open question reaches the WAITING_FOR_ARCHITECTURE_APPROVAL
    # gate; a question routes back through the WAITING_FOR_USER_INPUT /
    # submit_answer loop instead.
    return _extract_pending_question(plan_text) is not None


def _extract_pending_question(plan_text):
    tail = _tail_for_clarification_check(plan_text or "")

    for sentence in re.split(r"(?<=[.?!])\s+", tail):
        if "?" not in sentence:
            continue

        if _SIGNOFF_PATTERNS.search(sentence):
            continue

        return sentence.strip()

    return None


def _roadmap_phase_id_for_build(build_id):
    from core.roadmap_engine import load_roadmap

    for phase in load_roadmap().get("phases", []):
        if phase.get("build_id") == build_id:
            return phase["id"]

    return None


def _create_architecture_approval(build):
    create_build_approval(
        build_id=build["id"],
        phase_id=_roadmap_phase_id_for_build(build["id"]),
        approval_type="architecture",
        title=f"Approve architecture plan for {build['name']}",
        description=build.get("plan") or "",
        risk=None,
        requested_action="approve_architecture",
    )


def _code_review_summary(build):
    # Surfaced alongside the security report at the deploy-approval human
    # gate -- purely informational, never used to auto-approve or
    # auto-block (the human decides, exactly as before 13R).
    code_review = build.get("code_review")

    if not code_review:
        return ""

    if code_review.get("skipped"):
        return f"\n\nCode review skipped: {code_review.get('reason')}."

    return f"\n\nAdvisory code review by {code_review.get('reviewer')}:\n{code_review.get('findings')}"


def _create_deploy_approval(build):
    security_report = build.get("security_report") or {}

    create_build_approval(
        build_id=build["id"],
        phase_id=_roadmap_phase_id_for_build(build["id"]),
        approval_type="deploy",
        title=f"Approve deployment for {build['name']}",
        description=f"{security_report.get('total_findings', 0)} security finding(s) found."
        + _code_review_summary(build),
        risk=security_report.get("highest_severity"),
        requested_action="approve_deploy",
    )


def _run_planning(build):
    try:
        _ensure_repo(build)
        result = delegate(
            _planning_prompt(build),
            task_type="planning",
            project_path=build["project_path"],
            timeout=PLANNING_TIMEOUT,
        )
    except Exception as error:
        # delegate() already tried every candidate provider (including
        # Claude as the final fallback) before raising -- this is a genuine
        # every-provider failure, not just "Claude is unavailable".
        transition(build, "FAILED", BUILD_TRANSITIONS)
        build["failure_reason"] = str(error)
        _record_if_terminal(build)
        return

    build["plan"] = result.get("response", "")
    build["planned_by"] = result.get("provider")

    if _looks_like_tool_call_leak(build["plan"]):
        rejections = build.get("_consecutive_planning_rejections", 0) + 1
        build["_consecutive_planning_rejections"] = rejections

        if rejections >= _MAX_CONSECUTIVE_REJECTIONS:
            transition(build, "FAILED", BUILD_TRANSITIONS)
            build["failure_reason"] = (
                f"{rejections} consecutive unusable planning responses"
            )
            _record_if_terminal(build)
        return

    build["_consecutive_planning_rejections"] = 0

    pending_question = _extract_pending_question(build["plan"])

    if pending_question is not None:
        build["pending_question"] = pending_question
        transition(build, "WAITING_FOR_USER_INPUT", BUILD_TRANSITIONS)
    else:
        transition(build, "WAITING_FOR_ARCHITECTURE_APPROVAL", BUILD_TRANSITIONS)
        _create_architecture_approval(build)


def _run_generation(build):
    try:
        _ensure_repo(build)
        delegated = delegate(
            _generation_prompt(build),
            task_type="coding",
            project_path=build["project_path"],
            timeout=GENERATION_TIMEOUT,
            capability="coding_agent",
        )
    except Exception as error:
        # delegate() already tried every candidate coding-capable provider
        # (Claude, then OpenCode) before raising -- a genuine every-provider
        # failure, not just "Claude is busy".
        transition(build, "FAILED", BUILD_TRANSITIONS)
        build["failure_reason"] = str(error)
        _record_if_terminal(build)
        return

    result = delegated["response"]
    build["generation_result"] = result
    build["generated_by"] = delegated["provider"]

    if not result.get("success") or _looks_like_no_op_generation(result):
        transition(build, "FAILED", BUILD_TRANSITIONS)
        build["failure_reason"] = (
            "Generation run did not complete successfully"
            if not result.get("success")
            else (
                "Generation reported success but made no changes (no files "
                "written, no commits) -- the coding agent stopped without "
                "actually doing the work despite exiting cleanly"
            )
        )
        _record_if_terminal(build)
        return

    # Cascade straight into the advisory code review within this same call
    # -- existing callers and tests rely on one advance_builds() taking a
    # successful generation all the way to WAITING_FOR_DEPLOY_APPROVAL, not
    # parking it in CODE_REVIEW for a later dispatch cycle to pick up.
    transition(build, "CODE_REVIEW", BUILD_TRANSITIONS)
    _run_code_review(build)


def _code_review_prompt(build):
    result = build.get("generation_result") or {}
    files_changed = result.get("files_changed") or []
    commits = result.get("commits") or []

    files_context = "\n".join(f"- {path}" for path in files_changed) or "(none reported)"
    commits_context = "\n".join(
        f"- {c.get('sha', '')} {c.get('message', '')}" for c in commits
    ) or "(none reported)"

    return (
        "You are performing an advisory code review of work another coding "
        "agent just generated on this project's current branch. Do NOT "
        "write, edit, or modify any files, and do NOT run commands that "
        "change anything -- read the changed files and recent git history "
        "if useful, and respond with text only.\n\n"
        f"Application: {build['name']}\n"
        f"Description: {build['description']}\n"
        f"Generated by: {build.get('generated_by')}\n\n"
        f"Files changed:\n{files_context}\n\n"
        f"Commits:\n{commits_context}\n\n"
        f"Agent's own summary:\n{result.get('response_text') or '(none)'}\n\n"
        "Review the diff/generated files for correctness problems, missing "
        "or unrun tests, and deviations from the approved plan. Your "
        "findings are advisory only -- a human makes the deploy decision. "
        "Reply with your findings, or state that you found no issues."
    )


def _claude_code_review(build):
    # Deliberately calls the claude provider directly rather than
    # delegate(task_type="review"): the review-role rotation could hand
    # this to openai/gemini, and the requirement is specifically Claude
    # oversight of non-Claude-generated work.
    if build.get("generated_by") == "claude":
        return {"skipped": True, "reason": "generated by claude"}

    provider = ai_provider.get_provider("claude")

    if provider is None or not provider["available_fn"]():
        return {"skipped": True, "reason": "claude unavailable"}

    try:
        findings = provider["run_text_task"](
            _code_review_prompt(build),
            timeout=CODE_REVIEW_TIMEOUT,
            project_path=build["project_path"],
        )
    except Exception as error:
        # Advisory checks must never stall the pipeline -- same "must not
        # stall Kai" pattern used throughout ai_router.py.
        return {"skipped": True, "reason": str(error)}

    return {"skipped": False, "reviewer": "claude", "findings": findings}


def _run_code_review(build):
    # Purely advisory Claude oversight of non-Claude-generated work. The
    # outcome (findings, skip, or failure) is recorded on the build and
    # surfaced at the WAITING_FOR_DEPLOY_APPROVAL human gate alongside the
    # security report -- it never auto-approves or auto-blocks anything.
    build["code_review"] = _claude_code_review(build)

    # What _run_generation used to do inline: security findings are
    # surfaced for a human to review via WAITING_FOR_DEPLOY_APPROVAL, never
    # used to silently auto-fail the build -- the same human-in-the-loop
    # pattern as every other approval gate here.
    transition(build, "SECURITY_REVIEW", BUILD_TRANSITIONS)
    build["security_report"] = run_all_scans(build["project_path"])
    transition(build, "WAITING_FOR_DEPLOY_APPROVAL", BUILD_TRANSITIONS)
    _create_deploy_approval(build)


def _run_deployment(build):
    result = deploy_build(build)
    build["deployment"] = result

    if result.get("deployed"):
        transition(build, "VERIFIED", BUILD_TRANSITIONS)
        transition(build, "COMPLETED", BUILD_TRANSITIONS)
    else:
        transition(build, "FAILED", BUILD_TRANSITIONS)
        build["failure_reason"] = result.get("reason", "Deployment failed")

    _record_if_terminal(build)

    if result.get("deployed"):
        # 17C: a successful self-modifying merge leaves the running
        # services executing stale pre-merge code (Python never hot-
        # reloads) -- restart whichever service actually imported the
        # changed modules. Persist the terminal COMPLETED state FIRST:
        # restarting ai-orchestrator.service takes down this very
        # process, and the restart must never be able to lose the
        # build's recorded outcome. Only runs on a successful deploy --
        # failed/rolled-back merges never reach this branch.
        _persist_build(build)
        # 17H: same class of gap as 17C, but for the CloudCLI plugin
        # bundle -- a merge that lands TypeScript source in the live
        # plugin repo is invisible until `npm run build` runs and the
        # compiled dist/ output reaches the directory CloudCLI serves
        # from. This must run BEFORE restart_services_if_needed: the
        # scheduler restart it queues kills this very process, and the
        # npm build has to finish first.
        redeploy_plugin_if_needed(build, result)
        restart_services_if_needed(build, result)


# CODE_REVIEW is dispatchable as a defensive fallback only -- the normal
# path cascades GENERATING -> CODE_REVIEW -> ... within one _run_generation
# call, so a build only sits in CODE_REVIEW if it was persisted mid-cascade
# (e.g. a crash between transitions).
_ACTIONABLE_STATUSES = {"REQUESTED", "PLANNING", "GENERATING", "CODE_REVIEW", "DEPLOYING"}


def _persist_build(build):
    # Update just this build's on-disk record by id, atomically (one flock
    # critical section) -- concurrent workers each persist their own build
    # without resaving, and clobbering, anyone else's record.
    def mutate(records):
        records = records if isinstance(records, list) else []

        for i, existing in enumerate(records):
            if existing.get("id") == build["id"]:
                records[i] = build
                return records

        records.append(build)
        return records

    update(BUILDS_FILE, mutate)


def _advance_one_build(build):
    try:
        status = build.get("status")

        if status == "REQUESTED":
            transition(build, "PLANNING", BUILD_TRANSITIONS)
            _run_planning(build)
        elif status == "PLANNING":
            _run_planning(build)
        elif status == "GENERATING":
            _run_generation(build)
        elif status == "CODE_REVIEW":
            _run_code_review(build)
        elif status == "DEPLOYING":
            _run_deployment(build)
    except Exception as error:
        # One build's crash must never lose track of another build's
        # concurrently-computed result inside the same pool.map call --
        # mark this build failed and persist it like any other outcome.
        transition(build, "FAILED", BUILD_TRANSITIONS)
        build["failure_reason"] = f"Unexpected error: {error}"
        _record_if_terminal(build)

    _persist_build(build)
    return build


def advance_builds():
    builds = load_builds()
    ready = [b for b in builds if b.get("status") in _ACTIONABLE_STATUSES]

    if not ready:
        return builds

    # Each ready build advances in its own worker, capped at
    # MAX_CONCURRENT_BUILDS. Builds are already isolated by design (own git
    # branch, own sandbox), and the work is I/O-bound (subprocess/HTTP), so
    # threads give real concurrency. With a single ready build this is
    # exactly the old sequential behavior. Builds not in `ready` are never
    # touched or resaved.
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BUILDS) as pool:
        list(pool.map(_advance_one_build, ready))

    return load_builds()
