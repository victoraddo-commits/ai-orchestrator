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
import core.ai.circuit_breaker as circuit_breaker
import core.ai.provider_latency as provider_latency
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
    #
    # 13M (2026-07-30): the direct Claude/Anthropic subscription's credit is
    # preserved by never trying "claude" first. The three alt-Claude routes
    # (Opus 4.7 + Sonnet 4.6 via the OpenRouter account, Fable 5 via OpenCode
    # Zen -- all billed independently of the subscription) formed a rotating
    # front group (CODING_ROTATING_FRONT, see _candidates_for), openrouter_
    # claude_opus first per explicit user directive (2026-07-30).
    #
    # 2026-08-02 operator directive: both the direct Claude/Anthropic
    # subscription AND the OpenRouter account are out of credit right now.
    # openrouter_claude_opus, openrouter_claude_sonnet, and opencode_deepseek
    # (all billed through the OpenRouter account, the last via opencode's
    # own stored OpenRouter credential) are pulled from this list entirely --
    # same "still registered in core.ai_provider, easy to re-add once quota
    # clears" treatment as gemini got the same day. opencode_claude (Fable 5,
    # billed through OpenCode Zen -- a separate, unaffected account) is now
    # CODING_ROTATING_FRONT's only member and this list's primary. Direct
    # "claude" stays in the fixed tail as a fallback (not disabled -- the
    # operator asked to reroute its work, not remove it) rather than being
    # pulled outright, so it resumes taking real traffic automatically once
    # its credit renews.
    #
    # qwen4_coding joined CODING_ROTATING_FRONT once tool-calling was
    # confirmed live on the RunPod pod (see core.ai_provider's
    # QWEN3_CODING_MODEL comment), rotating 50/50 with Fable 5 at first --
    # later the same day, explicit operator directive assigned it the
    # roadmap outright ("priority is the roadmap... qwen3 builds and fable
    # checks and approves"): qwen4_coding is now CODING_ROTATING_FRONT's
    # sole member and this list's primary, maximizing real usage of the
    # pay-per-use GPU rather than splitting its attempts with Fable 5.
    # opencode_claude drops to the fixed tail's first entry -- still the
    # immediate fallback if qwen4_coding fails, but its new primary job is
    # advisory code review (core.build_manager._opencode_claude_code_review,
    # née _claude_code_review) rather than co-primary generation. "fable...
    # approves" is advisory only, same as the pre-existing code review step
    # it replaced -- it does not call approve_architecture/approve_deploy
    # (see tests/test_kai_identity.py's structural guarantee); a human still
    # makes every approve/reject decision.
    # 2026-08-05: Qwen4 (pod ldtqgcshb2dwsw, RTX PRO 6000 96GB) is now the
    # primary for every role — qwen4_coding for coding_agent work,
    # qwen4_text for all text_task roles. All other providers shifted
    # to fallback positions. 8-way concurrency verified live.
    # 2026-08-07: qwen4_coding/qwen4Z removed — both RunPod pods OFFLINE.
    # opencode_claude (Fable 5 via OpenCode Zen) is now the primary coding
    # agent — separate billing from CloudCLI/Anthropic subscription.
    "coding": [
        "opencode_claude",
        "opencode_claude_sonnet",
        "opencode_claude_opus",
        "omniroute",
        "claude",
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
    #
    # gemini is listed first on real evidence (97.6% success, see the
    # comparison above) and, like "architecture" below, is in
    # FIXED_ORDER_TASK_TYPES -- rotation was silently undoing that evidence
    # by giving every candidate an equal first-try turn, so in practice
    # gemini was only tried first ~1-in-4 calls despite being the
    # demonstrably better choice. User flagged this live 2026-07-31 ("why is
    # kai not using gemini mainly for planning where it exceeds").
    # 2026-08-02 operator directive: gemini is quota-exhausted (429, its own
    # Google billing) -- delegated its primary slot to deepseek_native_flash
    # (native api.deepseek.com, no shared-quota exposure to gemini's or
    # OpenRouter's outage). Initially moved to last rather than removed;
    # operator then directed disabling it outright ("disable gemini for
    # now") after an 18-phase pileup confirmed every one of those failures
    # traced to this same gemini/openrouter quota wall -- removed from every
    # role's candidate list below, not merely deprioritized.
    #
    # Re-enabled later the same day ("gemini credit has been reloaded") --
    # restored to the front per the original 2026-07-31 evidence (97.6%
    # success, see comparison above); everything added to this role while
    # gemini was out (opencode_claude, deepseek_native_pro, and their
    # rationale below) stays, just behind gemini again now that its quota
    # problem is gone.
    # openrouter dropped 2026-08-02 (OpenRouter account out of credit, same
    # operator directive as "coding" and "architecture" below). opencode_claude
    # (Fable 5, via its new text_task route -- see core.ai_provider's
    # _opencode_claude_run_text_task) added same day per operator directive:
    # Fable 5 answers Kai's operator-chat questions (this role is what
    # core.ai.ai_router.chat/Kai's Telegram Q&A actually calls) alongside the
    # deepseek family. Never touches approvals -- those stay human-only,
    # unrelated to this role entirely.
    #
    # deepseek_native_pro joins the same day, explicit operator directive
    # ("assign DeepSeek-V4-Pro to help kai with the chats") -- another real,
    # independently-billed (native api.deepseek.com, no OpenRouter/Zen quota
    # exposure) candidate for Kai's Q&A redundancy/quality, alongside its
    # already-routed sibling deepseek_native_flash.
    # 17Z: qwen4_text (self-hosted RunPod RTX 5090) added as fallback
    # capacity after every primary provider and before the universal claude
    # tail in every text-task role below -- this is capacity, not a
    # replacement for anything currently working.
    # 2026-08-06: Dual-pod architecture — Pod A (ldtqgcshb2dwsw, qwen4_text)
    # is the GENERATOR primary for every text-task role. Pod B (60jwzf36623b0o,
    # qwen4_pod_b) is the REVIEW/DEPLOY primary — it never waits behind Pod A's
    # generation queue. Each pod is a physically separate RTX PRO 6000 GPU.
    # 2026-08-07: qwen4_text/qwen4_pod_b removed — RunPod pods OFFLINE.
    # deepseek_native_flash is now primary — 100% reliable, ~$20/month.
    "planning": ["deepseek_native_flash", "deepseek_native_pro", "gemini", "geminix", "omniroute_deepseek_flash", "opencode_claude", "claude"],
    # 2026-08-07: deepseek_native_pro primary for architecture — strongest
    # reasoning available, ~$5-10/month for sparing use.
    "architecture": ["deepseek_native_pro", "deepseek_native_flash", "omniroute_deepseek_flash", "gemini", "geminix", "claude"],
    "log_analysis": ["deepseek_native_flash", "groq", "omniroute_deepseek_flash", "claude"],
    "documentation": ["deepseek_native_flash", "omniroute_deepseek_flash", "groq", "claude"],
    # 2026-08-07: qwen4_pod_b removed — deepseek_native_flash takes primary.
    "review": ["deepseek_native_flash", "omniroute_deepseek_flash", "gemini", "geminix", "claude"],
    # 2026-08-07: groq stays primary for classification (fast/cheap, suited for
    # small structured calls). deepseek_native_flash is next fallback.
    "classification": ["groq", "deepseek_native_flash", "omniroute_deepseek_flash", "gemini", "geminix", "claude"],
}

# 2026-07-31: Law Tutor bot (core.law_tutor) -- a completely separate product
# domain (legal education for one specific user, not software operations),
# kept in its own "law_*" namespace rather than mixed into the roles above so
# its usage/evidence never blends with this system's own operational
# history. Every entry here is FIXED_ORDER (see below): these are deliberate
# task-fit assignments from real reasoning about each provider's strengths
# (gemini's long context for textbooks, claude's depth for case analysis,
# groq's speed for quick chat), the same rationale "architecture" was given
# fixed-order treatment for from day one -- not a claim that evidence
# already backs this ordering, since it's brand new.
LAW_TUTOR_ROLE_PROVIDERS = {
    # 2026-08-07: qwen4_text removed — RunPod pods OFFLINE.
    "law_document": ["gemini", "geminix", "deepseek_native_flash", "omniroute_deepseek_flash", "claude"],
    "law_case_analysis": ["claude", "deepseek_native_flash", "omniroute_deepseek_flash"],
    "law_teaching": ["deepseek_native_flash", "claude"],
    "law_exam": ["claude"],
    "law_flashcards": ["groq", "claude"],
    "law_chat": ["groq", "claude"],
    # Vision task types — Gemma 4 31B IT via GPU.ai primary, handles images/scans
    "law_document_vision": ["gpuai_gemma", "claude"],
}

# 2026-08-03: Juris Kai Legal Expert - Phase 17Z
# Legal teaching and explanation - requires strong comprehension and educational ability
# Legal case analysis - needs deep reasoning, precedent understanding, and case interpretation
# Legal research - needs accuracy, reliable information sources, and document analysis
# Legal argument construction - requires strong logical reasoning and persuasive writing
# Flashcard generation - needs structured output and organization
# General legal chat - needs quick responses and conversational ability
JURIS_KAI_ROLE_PROVIDERS = {
    # 2026-08-07 (late): deepseek_native_pro is now primary for ALL juris_* roles.
    # deepseek_native_flash was returning empty responses for legal article queries
    # (e.g. "Article 161") because it's a small/flash model that either content-filters
    # or simply cannot handle specific legal statute references.
    # deepseek_native_pro (DeepSeek-V4 Pro) is the full-size model with proper legal
    # reasoning — its architecture primary status already proves it handles complex
    # structured queries. No other task types share this chain — juris-kai has its
    # OWN dedicated routing, never sharing a bridge with general-purpose tasks.
    #
    # Fallback chain: deepseek_native_pro → gemini → omniroute_deepseek_flash → groq
    # (claude removed from juris fallbacks — Anthropic subscription out of credit
    #  as of 2026-08-07 and cannot serve as a viable fallback for legal queries).
    "juris_legal_teaching": ["deepseek_native_pro", "gemini", "groq"],
    "juris_case_analysis": ["deepseek_native_pro", "gemini", "omniroute_deepseek_flash"],
    "juris_research": ["deepseek_native_pro", "gemini", "geminix", "omniroute_deepseek_flash"],
    "juris_argument_construction": ["deepseek_native_pro", "gemini", "groq"],
    "juris_flashcards": ["deepseek_native_pro", "groq"],
    "juris_chat": ["deepseek_native_pro", "groq", "gemini"],
    # Vision task types — Gemma 4 31B IT via GPU.ai primary, handles images/scans
    "juris_document_vision": ["gpuai_gemma"],
}

ROLE_PROVIDERS.update(LAW_TUTOR_ROLE_PROVIDERS)
ROLE_PROVIDERS.update(JURIS_KAI_ROLE_PROVIDERS)

# 2026-08-07: Legal module coding — opencode_claude (Fable 5 via OpenCode Zen).
# Was qwen4_coding (RunPod) but both pods OFFLINE since Aug 6.
ROLE_PROVIDERS["legal_coding"] = ["opencode_claude"]

CHAT_HISTORY_MAX_MESSAGES = 40

DEFAULT_TASK_TYPE = "coding"

# 13V: task types whose ROLE_PROVIDERS order is a strict priority list --
# 13J's rotation (spread first-try traffic across candidates) is exactly
# wrong for a chain whose whole point is "this provider is the primary,
# the rest are redundancy". "planning" joined 2026-07-31: gemini's ordering
# there is backed by real success-rate evidence (see ROLE_PROVIDERS
# comment), not just a preference, so it deserves the same strict-priority
# treatment as "architecture" rather than being rotated down to a 1-in-4
# chance of going first. The law_* roles joined the same day for the same
# reason "architecture" originally did: each ordering is a deliberate
# task-fit judgment call (see LAW_TUTOR_ROLE_PROVIDERS comment), not a
# default that rotation should spread load across.
FIXED_ORDER_TASK_TYPES = frozenset({
    "architecture", "planning",
    "law_document", "law_case_analysis", "law_teaching", "law_exam", "law_flashcards", "law_chat",
    "law_document_vision",
    "juris_legal_teaching", "juris_case_analysis", "juris_research",
    "juris_argument_construction", "juris_flashcards", "juris_chat",
    "juris_document_vision",
})

USAGE_HISTORY_FILE = "ai_usage_history.json"
MAX_DESCRIPTION_LENGTH = 200


class AllProvidersFailed(Exception):
    """Raised when every candidate provider for a task type is unavailable or fails.

    Carries the structured per-candidate attempt log (same shape delegate()
    returns under "attempts" when return_attempts=True) so callers like
    core.ai.chief_architect can report *why* the chain was exhausted without
    parsing the message string.
    """

    def __init__(self, message, attempts=None):
        super().__init__(message)
        self.attempts = attempts or []


def classify_task(description):
    text = description.lower()

    scores = {
        category: sum(1 for keyword in keywords if keyword in text)
        for category, keywords in TASK_TYPE_KEYWORDS.items()
    }

    best_category = max(scores, key=scores.get)

    return best_category if scores[best_category] > 0 else DEFAULT_TASK_TYPE


# 13M: the coding role's rotating front group -- the alt-Claude routes that
# take turns as the first attempt (see ROLE_PROVIDERS["coding"]'s comment).
# Uniformly rotating the whole coding list, as every other role does, would
# put direct "claude" first on a fraction of calls, defeating the
# credit-preservation goal -- so only these rotate; every other coding
# candidate is a fixed tail, always tried last and in ROLE_PROVIDERS order.
# 2026-08-07: opencode_claude (Fable 5 via OpenCode Zen) is the only
# working coding agent — qwen4_coding/qwen4Z removed (RunPod pods OFFLINE).
CODING_ROTATING_FRONT = ["opencode_claude"]

# --- Purge disabled providers from all routing on import ---
# Reads memory/provider_state.json and removes any provider whose persisted
# state is `false` (or `{"enabled": false}`) from every role list and the
# coding rotating front.  The dashboard toggle writes this file; this block
# ensures Kai's in-memory routing mirrors it immediately on restart without
# manual edits to ROLE_PROVIDERS.
import json as _json
from pathlib import Path as _Path

_STATE_PATH = _Path(__file__).parent.parent.parent / "memory" / "provider_state.json"
try:
    if _STATE_PATH.exists():
        _raw = _json.loads(_STATE_PATH.read_text())
        _disabled = {
            name for name, val in _raw.items()
            if val is False or (isinstance(val, dict) and val.get("enabled") is False)
        }
        if _disabled:
            # ROLE_PROVIDERS (includes LAW_TUTOR + JURIS_KAI merged in)
            for _role, _providers in ROLE_PROVIDERS.items():
                ROLE_PROVIDERS[_role] = [p for p in _providers if p not in _disabled]
            # LAW_TUTOR_ROLE_PROVIDERS
            for _role, _providers in LAW_TUTOR_ROLE_PROVIDERS.items():
                LAW_TUTOR_ROLE_PROVIDERS[_role] = [p for p in _providers if p not in _disabled]
            # JURIS_KAI_ROLE_PROVIDERS
            for _role, _providers in JURIS_KAI_ROLE_PROVIDERS.items():
                JURIS_KAI_ROLE_PROVIDERS[_role] = [p for p in _providers if p not in _disabled]
            # CODING_ROTATING_FRONT
            CODING_ROTATING_FRONT = [p for p in CODING_ROTATING_FRONT if p not in _disabled]
except Exception:
    pass  # never let a bad state file break imports


def _candidates_for(task_type):
    if task_type == "coding":
        # Derived from ROLE_PROVIDERS["coding"] (not hardcoded) so tests and
        # callers that override the role list keep working: only the members
        # of CODING_ROTATING_FRONT actually present in the list rotate.
        candidates = ROLE_PROVIDERS.get("coding", ["claude"])
        front = [name for name in CODING_ROTATING_FRONT if name in candidates]
        tail = [name for name in candidates if name not in CODING_ROTATING_FRONT]
        return _rotate_candidates(task_type, front) + tail
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
#
# The OpenCode Zen phrases below are best-effort guesses, NOT yet confirmed
# against a real captured error string the way "weekly limit"/"usage limit"
# were for Claude -- user directive 2026-07-30, after every opencode_*
# provider failed generically ("generation did not succeed") during what
# turned out to be a real Zen credit exhaustion, indistinguishable from any
# other opaque failure because core.opencode_bridge silently discarded the
# actual subprocess stderr in that path (fixed same day -- see
# opencode_bridge.run_coding_task). Once a real example is captured through
# that fix, replace/tighten these guesses with the confirmed wording.
_QUOTA_EXCEEDED_MARKERS = (
    "weekly limit", "usage limit",
    "insufficient credit", "insufficient balance", "insufficient funds",
    "out of credit", "credit balance", "payment required", "billing",
)

# Providers driven through the shared OpenCode Zen credential -- a credit
# exhaustion there is a single account-wide event, not provider-specific,
# so it's worth a human notification the first time it's seen (not on every
# subsequent call that also fails the same known way).
_OPENCODE_ZEN_PROVIDERS_PREFIX = "opencode"


def _notify_opencode_quota_exceeded(provider_name, detail):
    from core.telegram_bridge import send_message

    try:
        send_message(
            f"OpenCode Zen credit exhausted (detected via {provider_name}): {detail}\n\n"
            "This account is shared across every opencode_* provider -- all of them "
            "will keep failing until credits are renewed."
        )
    except Exception as error:
        from core.logger import info
        info(f"telegram notify (opencode quota) failed: {type(error).__name__}")


def _record_coding_failure_health(provider_name, detail):
    was_quota_exceeded = (
        provider_health.get_quota_snapshot(provider_name) or {}
    ).get("status") == "quota_exceeded"

    if any(marker in detail.lower() for marker in _QUOTA_EXCEEDED_MARKERS):
        provider_health.capture_quota_exceeded(provider_name, detail=detail)
        if not was_quota_exceeded and provider_name.startswith(_OPENCODE_ZEN_PROVIDERS_PREFIX):
            _notify_opencode_quota_exceeded(provider_name, detail)
    else:
        provider_health.capture_provider_error(provider_name, detail=detail)


# 13V: classify a *call* failure (the candidate was tried and failed) into
# the failover-reason vocabulary {quota_exceeded, timeout, degraded_health,
# error} using existing provider_health signals rather than guessing:
#
#   * delegate() never calls a candidate whose snapshot already says
#     quota_exceeded (it's skipped beforehand), so a quota_exceeded snapshot
#     observed *after* the call must have been captured during it (e.g.
#     llm_clients._post_json's 429 handler or
#     _record_coding_failure_health) -- a verified, fresh quota signal.
#   * the confirmed-live _QUOTA_EXCEEDED_MARKERS wording counts too (same
#     markers _record_coding_failure_health uses to record the signal).
#   * timeouts surface as exception text ("...request failed: ReadTimeout",
#     "timed out after Ns") -- matched on wording, since requests exceptions
#     are re-raised type-name-only by llm_clients.
#   * an "error" snapshot (capture_provider_error) is provider_health's
#     degraded-but-not-verified-quota state.
def _classify_failure_reason(provider_name, detail):
    snapshot = provider_health.get_quota_snapshot(provider_name)
    status = (snapshot or {}).get("status")

    lowered = detail.lower()

    if status == "quota_exceeded" or any(marker in lowered for marker in _QUOTA_EXCEEDED_MARKERS):
        return "quota_exceeded"
    if "circuit" in lowered or "open" in lowered:
        return "circuit_open"
    if "latency degraded" in lowered:
        return "degraded_health"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if status == "error":
        return "degraded_health"
    return "error"


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


def delegate(description, task_type=None, timeout=60, project_path=None, capability="text_task", return_attempts=False, requires_file_access=False):
    # 13V: return_attempts=True adds an "attempts" key to the result -- the
    # structured log of every candidate that failed before the winner, each
    # {"provider", "error_type", "error"} with error_type in
    # {quota_exceeded, timeout, unavailable, degraded_health, error}. The
    # default return shape is unchanged. On full exhaustion the same log
    # rides on AllProvidersFailed.attempts regardless of the flag.
    #
    # 17R: requires_file_access=True signals that this call needs a provider
    # that can read/write files (coding_agent capability); text-only
    # providers are filtered out entirely rather than deprioritized.
    resolved_type = task_type or classify_task(description)
    candidates = _candidates_for(resolved_type)
    # "coding" is excluded from the outer rotation: _candidates_for already
    # rotated its alt-Claude front group internally (13M), so rotating the
    # returned list again would both double-advance the rotation state and
    # let the fixed last-resort tail (direct claude, opencode, ...) take
    # first-try turns.
    if resolved_type not in FIXED_ORDER_TASK_TYPES and resolved_type != "coding":
        candidates = _rotate_candidates(resolved_type, candidates)

    # 17R: wall-clock latency degradation -- demote providers whose latency
    # has spiked significantly above their historical baseline rather than
    # hard-excluding them. Degraded providers are tried only after all
    # healthy ones have been exhausted.
    healthy_candidates = [n for n in candidates if not provider_latency.is_latency_degraded(n)]
    latency_degraded_candidates = [n for n in candidates if provider_latency.is_latency_degraded(n)]
    candidates = healthy_candidates + latency_degraded_candidates

    attempts = []

    def record_failure(name, error_type, detail):
        attempts.append({"provider": name, "error_type": error_type, "error": detail})

    for name in candidates:
        provider = ai_provider.get_provider(name)

        if provider is None:
            record_failure(name, "unavailable", "not registered")
            continue

        if not provider["available_fn"]():
            record_failure(name, "unavailable", "not available (no credentials configured)")
            continue

        # Provider toggle: operator can disable a provider to force it offline
        if not provider.get("enabled", True):
            record_failure(name, "disabled", "operator disabled this provider")
            continue

        # Only a verified quota_exceeded status skips the call outright --
        # a plain "error" status is deliberately not treated the same way
        # (see provider_health.capture_provider_error's docstring: it can't
        # distinguish a real limit from a transient blip), so those
        # candidates still get a real attempt.
        quota = provider_health.get_quota_snapshot(name)
        if quota and quota.get("status") == "quota_exceeded":
            record_failure(name, "quota_exceeded", f"skipped, known quota_exceeded ({quota.get('detail')})")
            continue

        run_fn = provider.get("run_coding_task" if capability == "coding_agent" else "run_text_task")
        if run_fn is None:
            record_failure(name, "unavailable", f"does not support {capability}")
            continue

        # 17R: file-access-aware routing -- when a caller needs file access,
        # skip text-only providers that lack it.
        if requires_file_access and "file_access" not in provider.get("capabilities", []):
            record_failure(name, "unavailable", f"does not support file access (text-only provider)")
            continue

        # 17R: circuit-breaker -- skip providers whose breaker is open
        # (consecutive errors exceeded threshold) until the cooldown elapses.
        if circuit_breaker.is_open(name):
            breaker = circuit_breaker.get_breaker_snapshot(name) or {}
            record_failure(name, "circuit_open", f"circuit breaker open ({breaker.get('consecutive_failures', '?')} consecutive failures)")
            continue

        # 17R: latency degradation demotion -- record the degraded state
        # so attempts log reflects the demotion, but still try the call
        # (this candidate arrived here from the latency_degraded reordering
        # above, after all healthy candidates were exhausted).
        if provider_latency.is_latency_degraded(name):
            latency = provider_latency.get_latency_snapshot(name) or {}
            record_failure(name, "degraded_health", (
                f"latency degraded (demoted, tried as last resort): "
                f"last {latency.get('last_duration_ms')}ms vs "
                f"ema {latency.get('ema_ms', '?')}ms"
            ))

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
            # 17R: record circuit breaker failure and latency on every error
            circuit_breaker.record_failure(name)
            provider_latency.record_latency(name, duration_ms)
            record_failure(name, _classify_failure_reason(name, str(error)), str(error))
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
            # 17R: record circuit breaker failure and latency
            circuit_breaker.record_failure(name)
            provider_latency.record_latency(name, duration_ms)
            record_failure(name, _classify_failure_reason(name, detail), detail)
            continue

        record_usage(name, resolved_type, description, success=True, duration_ms=duration_ms, cost=_response_cost(response))
        # A real success is the strongest signal a stale/wrong quota_exceeded
        # verdict is out of date -- clear it immediately rather than waiting
        # out provider_health.QUOTA_EXCEEDED_EXPIRY_SECONDS.
        provider_health.clear_quota_exceeded(name)
        # 17R: success clears circuit breaker and records healthy latency
        circuit_breaker.record_success(name)
        provider_latency.record_latency(name, duration_ms)

        result = {
            "provider": name,
            "task_type": resolved_type,
            "response": response,
            "duration_ms": duration_ms,
        }
        if return_attempts:
            result["attempts"] = attempts
        return result

    raise AllProvidersFailed(
        f"No available provider could handle task_type={resolved_type!r}: "
        + "; ".join(f"{a['provider']}: {a['error']}" for a in attempts),
        attempts=attempts,
    )


def chat(messages, signals):
    # 17V: delegate prompt construction to the conversation memory module,
    # which injects long-term operator context, compressed history, and
    # preserved citations/directives before the recent messages.
    from core.kai.conversation import build_chat_prompt

    prompt = build_chat_prompt(messages, signals)

    result = delegate(prompt, task_type="planning", capability="text_task")

    return result["response"]


def get_worker_details():
    """15G: Per-worker detail view — performance trends, current task, queue
    depth. Reuses existing usage history and provider health data."""
    history = get_usage_history()
    providers = ai_provider.list_providers()
    quota_data = provider_health.get_all_snapshots() or {}

    from core.build_manager import load_builds as _load_builds
    all_builds = []
    try:
        all_builds = _load_builds() or []
    except Exception:
        pass

    workers = {}
    for name, info in providers.items():
        attempts = [e for e in history if e["provider"] == name]
        successes = [e for e in attempts if e["success"]]
        total = len(attempts)
        success_rate = round(len(successes) / max(total, 1) * 100, 1)

        # Current task — which build is this provider generating right now?
        current_build = None
        for b in all_builds:
            if b.get("generated_by") == name and b.get("status") == "GENERATING":
                current_build = {"id": b.get("id", "")[:8], "name": b.get("name", "?")}
                break

        # Avg duration from recent attempts
        durations = [e.get("duration_ms", 0) for e in attempts[-20:] if e.get("duration_ms")]
        avg_duration = round(sum(durations) / max(len(durations), 1))

        # Queue depth = builds waiting that this provider could handle
        queue_depth = len([b for b in all_builds if b.get("status") in ("ARCHITECTURE_APPROVED",)])

        quota = quota_data.get(name, {}) if isinstance(quota_data, dict) else {}
        quota_status = quota.get("status", "ok") if isinstance(quota, dict) else "ok"

        workers[name] = {
            "name": name,
            "description": info.get("description", ""),
            "available": info.get("available", False),
            "enabled": info.get("enabled", True),
            "capabilities": info.get("capabilities", []),
            "cost_tier": info.get("cost_tier", "unknown"),
            "quota_status": quota_status,
            "success_rate": success_rate,
            "total_attempts": total,
            "successes": len(successes),
            "current_task": current_build,
            "queue_depth": queue_depth,
            "avg_duration_ms": avg_duration,
        }

    return workers


def get_provider_dashboard():
    history = get_usage_history()
    providers = ai_provider.list_providers()

    # 13J: cross-reference builds to show current job and queue depth per
    # provider.  Imported lazily -- core.build_manager imports ai_router at
    # module level.
    from core.build_manager import load_builds as _load_builds

    builds = []
    try:
        builds = _load_builds() or []
    except Exception:
        builds = []

    running_builds = {
        b["generated_by"]: b
        for b in builds
        if b.get("status") == "GENERATING" and b.get("generated_by")
    }

    actionable = {"REQUESTED", "PLANNING", "ARCHITECTURE_APPROVED", "GENERATING"}

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

        # 13J: derive a health status word from the available signals.
        success_rate = (len(successes) / len(attempts)) if attempts else None
        current_job = running_builds.get(name)

        # queue_depth is the number of builds that are waiting for a slot
        # (actionable but not currently assigned to *this* provider) plus
        # any running build on this provider -- i.e. the total active work
        # pool the provider might serve.
        own_job_id = current_job["id"] if current_job else None
        queue_depth = sum(
            1 for b in builds
            if b.get("status") in actionable
            and b.get("id") != own_job_id
        )

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
            # 13J additions:
            "health": _derive_health(name, info, attempts, success_rate, quota, current_job),
            "success_rate": success_rate,
            "current_job": current_job["id"] if current_job else None,
            "current_job_name": current_job["name"] if current_job else None,
            "queue_depth": queue_depth,
            # 17R additions: circuit breaker and latency snapshots
            "circuit_breaker": circuit_breaker.get_breaker_snapshot(name),
            "latency": provider_latency.get_latency_snapshot(name),
            "file_access": "file_access" in info.get("capabilities", []),
        }

    return dashboard


def _derive_health(name, info, attempts, success_rate, quota, current_job):
    if not info["available"]:
        return "unknown"
    if circuit_breaker.is_open(name):
        return "circuit_open"
    if provider_latency.is_latency_degraded(name):
        return "latency_degraded"
    if quota and quota.get("status") == "quota_exceeded":
        return "quota_exceeded"
    if quota and quota.get("status") == "error":
        return "degraded"
    if current_job is not None:
        return "busy"
    if not attempts:
        return "idle"
    if success_rate is not None:
        if success_rate >= 0.8:
            return "healthy"
        if success_rate >= 0.5:
            return "degraded"
        return "error"
    return "healthy"


def remove_provider_from_roles(name: str) -> int:
    """Remove a provider from every role list in the router.

    Cleans ROLE_PROVIDERS, LAW_TUTOR_ROLE_PROVIDERS, JURIS_KAI_ROLE_PROVIDERS,
    and CODING_ROTATING_FRONT.  Returns the number of lists the provider was
    removed from.
    """
    count = 0
    for role, providers in list(ROLE_PROVIDERS.items()):
        if name in providers:
            providers.remove(name)
            count += 1
    # Also clean the sub-role dicts that get merged in
    for sub_roles in (LAW_TUTOR_ROLE_PROVIDERS, JURIS_KAI_ROLE_PROVIDERS):
        for role, providers in list(sub_roles.items()):
            if name in providers:
                providers.remove(name)
                count += 1
    global CODING_ROTATING_FRONT
    if name in CODING_ROTATING_FRONT:
        CODING_ROTATING_FRONT = [n for n in CODING_ROTATING_FRONT if n != name]
        count += 1
    return count
