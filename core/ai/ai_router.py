"""Phase 12J: multi-AI provider router.

Routes a task to the best-fit provider by role, not by "wait until Claude's
credits run out" -- Gemini/Groq are used for their designated roles from the
first task that matches, per the AI-team model:

    Claude: coding, architecture implementation, difficult debugging
    Gemini: reviews, planning, documentation, architecture critique
    Groq:   logs, quick analysis, simple tasks

Falls back through each role's candidate list on unavailability or failure,
ultimately landing on Claude (the one provider guaranteed capable of
anything) if every role-specific candidate fails. Every attempt -- success
or failure -- is recorded to memory/ai_usage_history.json.

Phase 13J: which candidate gets tried *first* now rotates per task_type
(memory/provider_rotation.json, see _rotate_candidates) instead of always
starting from index 0 -- otherwise a rarely-failing primary starves every
other candidate of real usage, leaving paid/loaded credit on providers
like openrouter/minimax/opencode_claude untouched. Fallback-on-failure
still walks the rest of the (rotated) list exactly as before.
"""

import time
from datetime import datetime

import core.ai_provider as ai_provider
import core.ai.provider_health as provider_health
from core.memory import load, save, update


TASK_TYPE_KEYWORDS = {
    "coding": ["build", "implement", "code", "debug", "fix", "develop", "create"],
    "planning": ["design", "architecture", "plan", "review", "critique"],
    "log_analysis": ["log", "logs", "analyze", "analysis", "quick"],
    "documentation": ["document", "documentation", "readme", "docs"],
}

# Ordered by preference within each role; every list ends with "claude" as
# the universal fallback, since it's the one provider guaranteed capable of
# any task type.
ROLE_PROVIDERS = {
    # Claude (CloudCLI/Anthropic subscription) stays first -- most proven,
    # deepest test/review discipline. On quota/limit exhaustion, opencode_claude
    # (Claude Fable 5 billed through OpenCode Zen's separate, well-funded
    # account) is the second try -- same model family, independent billing,
    # and deliberately maximizes Zen credit usage per user directive
    # (2026-07-28). If Fable 5 is *also* unavailable/failing (confirmed live
    # 2026-07-29: the CloudCLI subscription hit its weekly limit mid-session),
    # escalate within the same Zen account to Sonnet 5 then Opus 5 before
    # giving up on "a real Claude model" entirely -- per user directive
    # (2026-07-29). Only after all three Claude-family options are exhausted
    # does the list fall through to "opencode" (DeepSeek V4 Pro, a different
    # model family) as the last resort. "opencode" was dropped 2026-07-28
    # while its only model was minimax-m2.7 (paused, see below); restored
    # 2026-07-29 now that core.opencode_bridge.OPENCODE_DEFAULT_MODEL points
    # at deepseek-v4-pro instead, via a dedicated OpenCode Zen credential
    # confirmed live (2026-07-29) to have full account access, not narrowly
    # scoped to DeepSeek -- it authenticates fine for the claude-fable-5/
    # sonnet-5/opus-5 routes too, so swapping it into the shared "opencode"
    # auth.json slot didn't risk any of them.
    #
    # 13U (2026-07-30) appends "opencode_deepseek" (openrouter/deepseek/
    # deepseek-v4-pro via the opencode CLI's shared openrouter auth slot,
    # not the Zen credential) between the Claude-family Zen tiers and the
    # generic Zen "opencode" route, plus "deepseek" (llm_clients.call_deepseek,
    # its own dedicated DEEPSEEK_OPENROUTER_API_KEY) on the planning/
    # documentation/review text_task roles.
    #
    # 13T (2026-07-29) appends "opencode_minimax" (opencode/minimax-m2.7,
    # pinned in ai_provider) -- the half of the 2026-07-28 blanket minimax
    # pause the usage history does *not* support. Reviewed via
    # core.ai.provider_evidence over memory/ai_usage_history.json, with the
    # "opencode" aggregate split per model by matching each entry's timestamp
    # and duration against the opencode CLI session store
    # (~/.local/share/opencode/opencode.db, session.model/time_updated;
    # every match was within 0.2s):
    #
    #   minimax-m2.7    3 attempts, 3 successes (100%)
    #   deepseek-v4-pro 4 attempts, 2 successes  (50%) -- both of the
    #                   provider's recorded failures were deepseek's: a 600s
    #                   wall-clock timeout (2026-07-29T04:40:03) and missing
    #                   memory/*.json tool errors (19:45:45, environmental,
    #                   fixed by a2ab1d4)
    #
    # Same-capability comparators over the same history: opencode_claude
    # (Fable 5) 4/8 with three timeouts, claude 3/5, opencode_claude_sonnet
    # 1/1. minimax-m2.7 is the only coding-agent model with zero recorded
    # timeouts, tool errors, or hallucinated-tool-call events on this path.
    #
    # Cross-checked against git history rather than trusting the success
    # flag alone (13S: flags were content-blind before 4c69637): commits
    # e622a14+34a7ac3 (13F, implementation + tests, merged to main via
    # 7bd8f73) and b17d199 (13G, API endpoints + 123 test lines) were both
    # authored inside a minimax session window. The third run (2640db8, 13R)
    # committed work authored during the *preceding* timed-out Fable session
    # -- a salvage, not original authorship -- so 2 of 3 are verified
    # original implementations, not 3.
    #
    # Placed last, deliberately: 3 attempts is below
    # provider_evidence.MIN_SAMPLE_SIZE, which caps it at "observe" rather
    # than "trusted". That earns a real slot in the rotation (see
    # _rotate_candidates -- every candidate gets a turn as primary, so this
    # is how it accumulates the evidence to be re-judged on), not a
    # promotion ahead of the Claude family, and not a change to
    # opencode_bridge.OPENCODE_DEFAULT_MODEL.
    "coding": [
        "claude",
        "opencode_claude",
        "opencode_claude_sonnet",
        "opencode_claude_opus",
        "opencode_deepseek",
        "opencode",
        "opencode_minimax",
    ],
    # minimax stays out of every text_task role -- unchanged from the
    # 2026-07-28 pause, but 13T re-grounded it in the full usage history
    # instead of the single 13P incident that triggered it:
    #
    #   minimax/planning  4 attempts, 3 flagged "success" (75%), 1
    #                     ConnectionError -- but all 3 of those flags predate
    #                     13S's plan validation (4c69637), when any HTTP 200
    #                     counted as success regardless of content. Checked
    #                     against the plan text actually stored in
    #                     memory/builds.json, every one of the 3 was
    #                     hallucinated <minimax:tool_call>/<invoke> markup
    #                     with no plan in it: ca7ff314/13P, 56e6c3d7/13R,
    #                     and e75e4848/13Q -- a third incident found only by
    #                     this review. Verified usable outputs: 0/4.
    #   comparators       gemini 41/42 (97.6%), openrouter 11/12 (91.7%)
    #
    # log_analysis and documentation have no recorded minimax attempts at
    # all, so on counts alone they'd be "insufficient_history" -- but they
    # reach minimax through the identical tools-less code path
    # (ai_provider._minimax_run_text_task -> llm_clients.call_minimax, no
    # tools wired up), which is the established cause of the failure, so the
    # planning evidence carries. The registry entry stays registered; only
    # the routing is withheld.
    "planning": ["gemini", "openrouter", "deepseek", "claude"],
    "log_analysis": ["groq", "openrouter", "claude"],
    "documentation": ["gemini", "groq", "openrouter", "deepseek", "claude"],
    # Phase 13D: the only task_type that puts OpenAI first -- every other
    # role already has a designated primary (Claude/Gemini/Groq), so OpenAI
    # had no route to ever be tried. Falls back to gemini then claude, same
    # universal-fallback convention as every other role above.
    "review": ["openai", "gemini", "deepseek", "claude"],
}

DEFAULT_TASK_TYPE = "coding"

USAGE_HISTORY_FILE = "ai_usage_history.json"
MAX_DESCRIPTION_LENGTH = 200


class AllProvidersFailed(Exception):
    """Raised when every candidate provider for a task type is unavailable or fails."""


def classify_task(description):
    text = description.lower()

    scores = {
        category: sum(1 for keyword in keywords if keyword in text)
        for category, keywords in TASK_TYPE_KEYWORDS.items()
    }

    best_category = max(scores, key=scores.get)

    return best_category if scores[best_category] > 0 else DEFAULT_TASK_TYPE


def _candidates_for(task_type):
    return ROLE_PROVIDERS.get(task_type, ["claude"])


ROTATION_STATE_FILE = "provider_rotation.json"


# ROLE_PROVIDERS order still matters as the *fallback* walk once a call
# starts -- but always starting from candidates[0] meant a rarely-failing
# primary (gemini for planning, claude for coding) starved everyone listed
# after it of real traffic, leaving paid/loaded credit on providers like
# openrouter, minimax, and opencode_claude untouched. Each task_type gets
# its own rotating start position instead: every delegate() call for that
# role tries the next candidate in line first, still falling through the
# rest of the (rotated) list on failure exactly as before.
#
# 13R: the whole read-increment-write of the rotation index goes through
# core.memory.update()'s single flock critical section -- with builds now
# dispatched concurrently (build_manager's thread pool), the previous
# load-then-save version let two simultaneous delegate() calls read the
# same index and land on the same starting provider, defeating rotation.
def _rotate_candidates(task_type, candidates):
    if len(candidates) <= 1:
        return candidates

    captured = {}

    def mutate(state):
        state = state if isinstance(state, dict) else {}
        start = state.get(task_type, 0) % len(candidates)
        captured["start"] = start
        state[task_type] = (start + 1) % len(candidates)
        return state

    update(ROTATION_STATE_FILE, mutate)

    start = captured["start"]
    return candidates[start:] + candidates[:start]


def get_usage_history():
    return load(USAGE_HISTORY_FILE) or []


def record_usage(provider, task_type, description, success, duration_ms, error=None, cost=None):
    history = get_usage_history()

    # 13W: cost is the *provider-reported* figure only (e.g. OpenCode's
    # step_finish events, which carry OpenRouter/Zen's real billed cost).
    # Providers that don't report one record null -- a number is never
    # estimated/fabricated from token counts here.
    entry = {
        "provider": provider,
        "task_type": task_type,
        "description": description[:MAX_DESCRIPTION_LENGTH],
        "success": success,
        "duration_ms": duration_ms,
        "error": error,
        "cost": cost,
        "timestamp": datetime.now().isoformat(),
    }

    history.append(entry)
    save(USAGE_HISTORY_FILE, history)

    return entry


# Confirmed live (2026-07-28): Claude Code returns this exact wording when
# the account's weekly usage limit is hit -- unlike ai_provider.py's
# _claude_run_text_task, which deliberately never pattern-matches since it
# had no verified example, this one is real and durable (not transient), so
# it's worth recording as quota_exceeded so K4's skip-known-quota-exceeded
# check actually prevents wasted retries for the rest of the reset window.
_QUOTA_EXCEEDED_MARKERS = ("weekly limit", "usage limit")


def _record_coding_failure_health(provider_name, detail):
    if any(marker in detail.lower() for marker in _QUOTA_EXCEEDED_MARKERS):
        provider_health.capture_quota_exceeded(provider_name, detail=detail)
    else:
        provider_health.capture_provider_error(provider_name, detail=detail)


# 13W: pull the provider-reported cost out of a response, if any. Only
# coding-agent responses (dicts from opencode_bridge/coding_bridge) can carry
# one today; text_task responses are plain strings and yield None.
def _response_cost(response):
    if not isinstance(response, dict):
        return None
    cost = response.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        return cost
    return None


def delegate(description, task_type=None, timeout=60, project_path=None, capability="text_task"):
    resolved_type = task_type or classify_task(description)
    candidates = _rotate_candidates(resolved_type, _candidates_for(resolved_type))

    failures = []

    for name in candidates:
        provider = ai_provider.get_provider(name)

        if provider is None:
            failures.append(f"{name}: not registered")
            continue

        if not provider["available_fn"]():
            failures.append(f"{name}: not available (no credentials configured)")
            continue

        # Only a verified quota_exceeded status skips the call outright --
        # a plain "error" status is deliberately not treated the same way
        # (see provider_health.capture_provider_error's docstring: it can't
        # distinguish a real limit from a transient blip), so those
        # candidates still get a real attempt.
        quota = provider_health.get_quota_snapshot(name)
        if quota and quota.get("status") == "quota_exceeded":
            failures.append(f"{name}: skipped, known quota_exceeded ({quota.get('detail')})")
            continue

        run_fn = provider.get("run_coding_task" if capability == "coding_agent" else "run_text_task")
        if run_fn is None:
            failures.append(f"{name}: does not support {capability}")
            continue

        start = time.time()
        try:
            if capability == "coding_agent":
                response = run_fn(project_path, description, timeout=timeout)
            else:
                response = run_fn(description, timeout=timeout, project_path=project_path)
        except Exception as error:
            duration_ms = int((time.time() - start) * 1000)
            record_usage(name, resolved_type, description, success=False, duration_ms=duration_ms, error=str(error))
            if capability == "coding_agent":
                _record_coding_failure_health(name, str(error))
            failures.append(f"{name}: {error}")
            continue

        duration_ms = int((time.time() - start) * 1000)

        # A coding_agent call can return normally (no exception) yet still
        # represent a failed generation (result["success"] is False) -- that
        # must fall through to the next candidate too, since a failed
        # generation is exactly the case that must not stall Kai.
        if capability == "coding_agent" and isinstance(response, dict) and not response.get("success"):
            duration_ms = int((time.time() - start) * 1000)
            detail = "; ".join(e.get("content", "") for e in response.get("tool_errors") or []) or "generation did not succeed"
            # A failed generation still incurred whatever cost the provider
            # reported for it -- record that too.
            record_usage(name, resolved_type, description, success=False, duration_ms=duration_ms, error=detail, cost=_response_cost(response))
            _record_coding_failure_health(name, detail)
            failures.append(f"{name}: {detail}")
            continue

        record_usage(name, resolved_type, description, success=True, duration_ms=duration_ms, cost=_response_cost(response))

        return {
            "provider": name,
            "task_type": resolved_type,
            "response": response,
            "duration_ms": duration_ms,
        }

    raise AllProvidersFailed(
        f"No available provider could handle task_type={resolved_type!r}: " + "; ".join(failures)
    )


def get_provider_dashboard():
    history = get_usage_history()
    providers = ai_provider.list_providers()

    dashboard = {}

    for name, info in providers.items():
        last_entry = next((e for e in reversed(history) if e["provider"] == name), None)

        attempts = [e for e in history if e["provider"] == name]
        successes = [e for e in attempts if e["success"]]

        # 13W: AI Workforce Analytics aggregates. total_cost sums only
        # provider-reported cost figures (entries recorded before 13W, or
        # from providers that report no cost, have cost=None and are simply
        # absent from the sum) -- it stays None, not 0.0, when no attempt
        # ever carried a real figure, so "no cost data" is never displayed
        # as "free". average_duration_ms covers every attempt, success or
        # failure, since duration_ms is recorded for both.
        costs = [e["cost"] for e in attempts if isinstance(e.get("cost"), (int, float)) and not isinstance(e.get("cost"), bool)]
        durations = [e["duration_ms"] for e in attempts if e.get("duration_ms") is not None]

        # Claude's "quota" isn't a provider-verified figure (see
        # provider_health.claude_usage_snapshot's docstring) -- keep it
        # visibly distinct from the other three's real/attempted quota data.
        # A recorded error (e.g. a failed call, possibly a usage limit) takes
        # priority over the self-tracked count -- that's more actionable
        # signal than "N requests logged".
        if name == "claude":
            recorded_error = provider_health.get_quota_snapshot("claude")
            quota = recorded_error if recorded_error and recorded_error.get("status") == "error" else provider_health.claude_usage_snapshot()
        else:
            quota = provider_health.get_quota_snapshot(name) or {
                "percent_remaining": None,
                "detail": "quota not yet checked -- no request has been made to this provider",
            }

        dashboard[name] = {
            "status": "connected" if info["available"] else "not_configured",
            "description": info["description"],
            "cost_tier": info["cost_tier"],
            "last_task_type": last_entry["task_type"] if last_entry else None,
            "last_success": last_entry["success"] if last_entry else None,
            "last_response_time_ms": last_entry["duration_ms"] if last_entry else None,
            "last_request_at": last_entry["timestamp"] if last_entry else None,
            "total_attempts": len(attempts),
            "total_successes": len(successes),
            "total_cost": sum(costs) if costs else None,
            "cost_reported_calls": len(costs),
            "average_duration_ms": (sum(durations) / len(durations)) if durations else None,
            "percent_remaining": quota.get("percent_remaining"),
            "quota_detail": quota.get("detail"),
        }

    return dashboard
