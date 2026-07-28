from core.memory import load, save
from core.lifecycle import new_object, transition, InvalidTransition
from core.coding_bridge import run_coding_task
from core.ai.ai_router import delegate, AllProvidersFailed
from core.repo_manager import create_local_repo
from core.project_templates import get_template
from core.security_scanner import run_all_scans
from core.deployment_manager import deploy_build
from core.remediation import attempt_rollback
from core.build_learning import record_build_outcome, TERMINAL_STATUSES

# Planning tasks are text-in/text-out and don't need Claude's file/tool
# access -- only actual code generation does. Routing them through the
# multi-provider AI router (Gemini/OpenRouter/Minimax first, Claude only as
# the guaranteed-capable last resort) means the step that runs for every
# roadmap phase attempt, and that caused every one of tonight's Claude-side
# incidents, no longer needs to touch Claude at all in the common case.
PLANNING_TIMEOUT = 180


# Deliberately a separate store from approval_queue.json -- that queue is
# swept every cycle by remediation_runner.process(), which calls
# execute_action() (docker-restart only) on every approved request
# regardless of action name. Build approvals need their own isolated queue
# so they never collide with that pipeline.
BUILDS_FILE = "builds.json"

# TESTING/SECURITY_REVIEW/DEPLOY_APPROVAL/DEPLOYING/VERIFIED are defined here
# for forward compatibility with Phases 12D-12G, which will add the logic
# that actually drives builds through them. Until then, advance_builds()
# only ever takes GENERATING to COMPLETED or FAILED directly.
BUILD_TRANSITIONS = {
    "REQUESTED": ["PLANNING", "FAILED"],
    "PLANNING": ["WAITING_FOR_USER", "FAILED"],
    "WAITING_FOR_USER": ["PLANNING", "ARCHITECTURE_APPROVED", "FAILED"],
    "ARCHITECTURE_APPROVED": ["GENERATING", "FAILED"],
    # SECURITY_REVIEW is reachable directly from GENERATING (not only via
    # TESTING) because TESTING has no automated logic yet -- see Phase 12E's
    # scoping note. COMPLETED stays reachable too, for the (currently
    # theoretical) case of a caller that wants to skip security review.
    "GENERATING": ["TESTING", "SECURITY_REVIEW", "COMPLETED", "FAILED"],
    "TESTING": ["SECURITY_REVIEW", "FAILED"],
    "SECURITY_REVIEW": ["DEPLOY_APPROVAL", "FAILED"],
    "DEPLOY_APPROVAL": ["DEPLOYING", "FAILED"],
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
        generation_result=None,
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

    _require_status(build, "WAITING_FOR_USER")

    def mutate(b):
        transition(b, "PLANNING", BUILD_TRANSITIONS)
        b.setdefault("qa_history", []).append({"answer": answer})

    return _update(build_id, mutate)


def approve_architecture(build_id, operator=None, note=None):
    build = get_build(build_id)
    if build is None:
        return None

    _require_status(build, "WAITING_FOR_USER")

    def mutate(b):
        transition(b, "ARCHITECTURE_APPROVED", BUILD_TRANSITIONS, note=note)
        b["architecture_approved_by"] = operator

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

    _require_status(build, "DEPLOY_APPROVAL")

    def mutate(b):
        transition(b, "DEPLOYING", BUILD_TRANSITIONS, note=note)
        b["deploy_approved_by"] = operator

    return _update(build_id, mutate)


def rollback_deployment(build_id):
    build = get_build(build_id)
    if build is None:
        return None

    deployment = build.get("deployment")
    if not deployment or not deployment.get("remediation_id"):
        raise ValueError(f"Build {build_id!r} has no deployment to roll back")

    attempt_rollback(deployment["remediation_id"])

    def mutate(b):
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


def _generation_prompt(build):
    return (
        "The following architecture plan was reviewed and approved by the "
        "requester. Implement it now: write the code, and commit your work "
        "with git as you go.\n\n"
        f"Application: {build['name']}\n"
        f"Description: {build['description']}\n"
        + _template_context(build)
        + f"Approved plan:\n{build.get('plan') or ''}"
    )


def _ensure_repo(build):
    create_local_repo(build["project_path"], branch=f"build-{build['id']}")


def _record_if_terminal(build):
    if build.get("status") in TERMINAL_STATUSES:
        record_build_outcome(build)


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
    transition(build, "WAITING_FOR_USER", BUILD_TRANSITIONS)


def _run_generation(build):
    try:
        _ensure_repo(build)
        result = run_coding_task(build["project_path"], _generation_prompt(build))
    except Exception as error:
        transition(build, "FAILED", BUILD_TRANSITIONS)
        build["failure_reason"] = str(error)
        _record_if_terminal(build)
        return

    build["generation_result"] = result

    if not result.get("success"):
        transition(build, "FAILED", BUILD_TRANSITIONS)
        build["failure_reason"] = "Generation run did not complete successfully"
        _record_if_terminal(build)
        return

    # Security findings are surfaced for a human to review via
    # DEPLOY_APPROVAL, never used to silently auto-fail the build -- the
    # same human-in-the-loop pattern as every other approval gate here.
    transition(build, "SECURITY_REVIEW", BUILD_TRANSITIONS)
    build["security_report"] = run_all_scans(build["project_path"])
    transition(build, "DEPLOY_APPROVAL", BUILD_TRANSITIONS)


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


def advance_builds():
    builds = load_builds()

    for build in builds:
        status = build.get("status")

        if status == "REQUESTED":
            transition(build, "PLANNING", BUILD_TRANSITIONS)
            _run_planning(build)
        elif status == "PLANNING":
            _run_planning(build)
        elif status == "GENERATING":
            _run_generation(build)
        elif status == "DEPLOYING":
            _run_deployment(build)

    save_builds(builds)

    return builds
